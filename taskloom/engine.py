"""Runs a flow: blocks in topological order, with retries, timeouts, error ports and cancellation."""

from __future__ import annotations

import datetime as dt
import decimal
import os
import pickle
import shutil
import threading
import time
import traceback
import uuid
from dataclasses import dataclass
from pathlib import Path

import polars as pl

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


def secret_forms() -> list[str]:
    """Every secret seen in this process (of 4 characters or more), also as repr() and JSON write it inside
    a quoted text (a \\ or quote escaped, ä as \\u00e4); longest first."""
    import json

    forms = {form for s in list(REVEALED) if len(s) >= 4
             for form in (s, repr(s)[1:-1], repr(s + "'\"")[1:-4], json.dumps(s)[1:-1],
                          json.dumps(s, ensure_ascii=False)[1:-1])}
    return sorted(forms, key=len, reverse=True)


def redact(value):
    """Replace every secret seen in this process, wherever it appears (also inside text that code built
    from it, like f"user:{password}", and written escaped, as in a list's repr), before anything is logged,
    stored or printed."""
    if isinstance(value, str):
        for secret in secret_forms():
            if secret in value:
                value = value.replace(secret, "***")
        return value
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


class Context:
    """What a block's run() receives as `ctx`."""

    def __init__(self, *, block_id, attempt, params, as_of, functions, workspace, home, emit, run_cancel,
                 attempt_cancel, answers=None, registry=None):
        self.block_id = block_id
        self.attempt = attempt
        self.params = dict(params)
        # The time this run is for (its now()), with the flow's time zone if it has one: the as_of of runs started
        # from this block.
        self.as_of = as_of
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
        rows = value.head(20)
        for secret in secret_forms():  # scrub the text cells, then cut them (a cut secret is no longer found)
            rows = rows.with_columns(pl.col(pl.String).str.replace_all(secret, "***", literal=True))
        with pl.Config(tbl_rows=20, tbl_cols=20, fmt_str_lengths=60, tbl_width_chars=200):
            return f"{value.summary()}\n{rows}"
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
             answers=None, as_of: dt.datetime | None = None, trigger: str | None = None,
             only: str | None = None, inputs_from: int | None = None, given: dict | None = None,
             run_from: str | None = None) -> RunResult:
    """Run a flow. `answers` (with a wait(ask_id, ctx) method) lets blocks ask the user questions.

    `trigger` is the Schedule block that started a scheduled run; None means a manual run.
    `only` runs just that block: with the inputs it got in run `inputs_from`, and the ones in
    `given` ({input: Python expression}; empty means no value) instead.
    `run_from` runs that block (with the inputs it got in run `inputs_from`) and every block after it;
    the blocks before them are not run again: what they held in run `inputs_from` is used.
    """
    errors, warnings = validate(flow, registry, home)
    if errors:
        raise FlowError(errors)
    now, funcs = run_clock(flow, home, as_of)
    values = P.resolve(flow.params, overrides or {}, funcs)
    texts = dict(given or {})  # as you typed them: shown again next time, and read again then
    start = only or run_from
    fired = _fired_triggers(flow, registry, trigger)
    part = {only} if only else blocks_from(flow, registry, run_from) if run_from else _line(flow, registry, fired)
    if start is not None:  # refuse before a run is recorded
        given = _evaluate_inputs(texts, values)
        _only_inputs(flow, registry, home, start, inputs_from, given, texts)
        if run_from:
            _from_before(flow, registry, home, part, inputs_from, run_from)
    started_by = (f"only {only}" if only else f"from {run_from}" if run_from
                  else ", ".join(b for b in flow.blocks if b in fired))
    cancel = cancel or threading.Event()

    history = History(home.history_db)
    run_id = history.start_run(flow.name, str(flow.path), values, _now(), started_by)
    all_sinks = (history, *sinks)
    running: dict = {}  # block -> attempt, for the blocks started and not finished

    def emit(event: dict):
        event = redact({"event": event.pop("event"), "run_id": run_id, "ts": _now(), **event})
        if event["event"] == "block_started":
            running[event["block"]] = event["attempt"]
        for sink in all_sinks:
            sink(event)
        if event["event"] == "block_finished":  # once every sink has it
            running.pop(event["block"], None)

    try:
        emit({"event": "run_started", "flow": flow.name, "params": {k: str(v) for k, v in values.items()},
              "blocks": sorted(part), "started_by": started_by})
        for message in registry.warnings + warnings:
            emit({"event": "log", "level": "WARNING", "message": message})
        runner = _Runner(flow, registry, home, values, now, funcs, emit, cancel, home.runs_dir / str(run_id), answers)
        runner.taking_part, runner.outside = part, set(flow.blocks) - part
        try:
            if start is not None:
                chosen = f"; inputs you chose: {', '.join(given)}" if given else ""
                earlier = f", with the inputs it got in {history.label(inputs_from)}" if inputs_from is not None else ""
                what = f"running only {start}" if only else f"running from {start} on (it and the blocks after it)"
                emit({"event": "log", "block": start, "level": "INFO", "message": f"{what}{earlier}{chosen}"})
                if inputs_from is not None:
                    # This run holds everything the run it starts from held (so retrying another block
                    # from it works, and its tables survive that run being cleaned up), then takes its
                    # inputs from that.
                    _carry_over(home.runs_dir / str(inputs_from), runner.workspace, _after(flow, start))
                given = {k: Table.from_frame(v, runner.workspace / start / "inputs" / f"{k}.parquet")
                         if Table.is_frame(v) else v for k, v in given.items()}
                runner.only, runner.run_from = only, run_from
                source = run_id if inputs_from is not None else None
                runner.only_inputs, runner.only_typed = _only_inputs(flow, registry, home, start, source, given, texts)
                if run_from:
                    runner.outputs, runner.blocked, runner.before_unhandled, runner.outside = _from_before(
                        flow, registry, home, part, source, run_from)
            status = runner.run()
        except BaseException:
            # Never leave the run, or a block of it, recorded as "running" (e.g. a forced second Ctrl+C).
            status = "cancelled" if cancel.is_set() else "failed"
            for block, attempt in list(running.items()):
                emit({"event": "block_finished", "block": block, "status": status, "attempts": attempt})
            emit({"event": "run_finished", "status": status})
            raise
        emit({"event": "run_finished", "status": status})
        _apply_retention(home, history)
        history.prune(int(home.settings()["history_days"]))
    finally:
        history.close()
    return RunResult(run_id, status, runner.outputs)


class _Runner:
    def __init__(self, flow, registry, home, values, now, funcs, emit, cancel, workspace, answers):
        self.flow, self.registry, self.home, self.answers = flow, registry, home, answers
        self.values, self.now, self.funcs = values, now, funcs
        self.emit, self.cancel, self.workspace = emit, cancel, workspace
        self.outputs: dict = {}
        self.blocked: set[str] = set()  # failed, or skipped because something before them failed

    only: str | None = None  # run just this block, with only_inputs
    only_inputs: dict = {}
    only_typed: dict = {}  # the ones you typed, as you typed them
    taking_part: set = set()  # the blocks this run takes part in: the only ones it runs and reports
    run_from: str | None = None  # run from this block on (with only_inputs): taking_part is it and the blocks after it
    outside: set = set()  # blocks taking no part in it (nor in the run it carries on from): see _gather
    before_unhandled: set = set()  # blocks before them whose failure nothing handles
    _stopped_by_before = False  # one of those stopped a block of this run: the run has failed

    def run(self) -> str:
        unhandled_failure = False
        for block_id in [b for b in order(self.flow) if b in self.taking_part]:
            if self.cancel.is_set():
                self.emit({"event": "block_finished", "block": block_id, "status": "cancelled"})
                continue
            alone = block_id in (self.only, self.run_from)  # its inputs were worked out for this run
            inputs = self.only_inputs if alone else self._gather(block_id)
            if inputs is None:
                continue
            if alone:  # what it gets (maybe values you chose), saved before it can change them
                self._keep_outputs(block_id, inputs, KEPT_INPUTS, typed=self.only_typed)
            status, produced = self._run_block(self.flow.blocks[block_id], inputs)
            self._keep_outputs(block_id, produced, status=status)
            if status == "failed":
                self.blocked.add(block_id)
            for port, value in produced.items():
                self.outputs[(block_id, port)] = value
            # Handled: an error handler of it runs next (not in a run of just this block).
            handled = self.only is None and any(
                e.src == block_id and e.src_port == ERROR_PORT and e.dst in self.taking_part for e in self.flow.edges)
            if status == "failed" and not handled:
                unhandled_failure = True
        if self.cancel.is_set():
            return "cancelled"
        return "failed" if unhandled_failure or self._stopped_by_before else "success"

    def _gather(self, block_id: str) -> dict | None:
        """The block's inputs from the blocks before it; None (and it is skipped) when it cannot run."""
        # A run-after connection from a block taking no part in the run does not hold it back.
        incoming = [e for e in self.flow.edges
                    if e.dst == block_id and not (e.dst_port == AFTER and e.src in self.outside)]
        # Nothing downstream of a failure runs: an edge from a normal output of a failed block (or of
        # a block skipped because of a failure) stops this block. Error outputs are the way out.
        if any(e.src in self.blocked and e.src_port != ERROR_PORT for e in incoming):
            self._stopped_by_before |= any(e.src in self.before_unhandled and e.src_port != ERROR_PORT
                                           for e in incoming)
            self.blocked.add(block_id)
            self._keep_outputs(block_id, {}, status=BLOCKED)
            self.emit({"event": "block_finished", "block": block_id, "status": "skipped",
                       "summary": "something before it failed"})
            return None
        in_ports = self._input_ports(self.flow.blocks[block_id])
        inputs, ready, after_failure, empty = {}, True, False, []
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
                self._stopped_by_before |= any(e.src in self.before_unhandled for e in missing)
                empty.append(name)
                continue
            # Several edges into one input give a list (in edge order), one edge gives the value.
            inputs[name] = delivered if getattr(port, "many", False) or len(edges) > 1 else delivered[0]
        if not ready:
            if after_failure:  # it did not get its input because something before it failed
                self.blocked.add(block_id)
            self._keep_outputs(block_id, {}, status=BLOCKED if after_failure else SKIPPED)
            why = "something before it failed" if after_failure else (
                "nothing reached its run-after" if empty[0] == AFTER else f"nothing reached its '{empty[0]}' input")
            self.emit({"event": "block_finished", "block": block_id, "status": "skipped", "summary": why})
            return None
        return inputs

    def _keep_outputs(self, block_id: str, produced: dict, file: str | None = None, status: str | None = None,
                      typed: dict | None = None):
        """Save what the block produced and how it ended (or, with KEPT_INPUTS, what it got and which of
        those you typed), so it can later feed a re-run of a single block. Values holding a password
        are never written to disk."""
        kept, withheld = {}, []
        for port, value in produced.items():
            value = _tables_to_marks(value)
            try:
                data = pickle.dumps(value)
            except Exception:
                withheld.append(port)  # not something that can be saved (e.g. an open connection)
                continue
            # A password, or text built from one (e.g. "user:" + password), stays off the disk.
            if _holds_secret(value) or any(len(s) >= 4 and s.encode() in data for s in REVEALED):
                withheld.append(port)
            else:
                kept[port] = value
        kept[WITHHELD] = withheld
        if status is not None:
            kept[STATUS] = status
        if typed:
            kept[TYPED] = typed
        folder = self.workspace / block_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / (file or KEPT_OUTPUTS)).write_bytes(pickle.dumps(kept))

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
                block_id=spec.id, attempt=attempt, params=self.values, as_of=self.now, functions=self.funcs,
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
            except Exception as e:
                # Stopped: whatever the block raised as it stopped (e.g. a closed query or connection) is not a failure.
                if isinstance(e, Cancelled) or self.cancel.is_set():
                    self.emit({"event": "block_finished", "block": spec.id, "status": "cancelled", "attempts": attempt})
                    return "cancelled", {}
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


KEPT_OUTPUTS = "outputs.pickle"
KEPT_INPUTS = "inputs.pickle"  # what a block run on its own got: its inputs then, not what came before it
STATUS = "__status__"  # how the block ended: success | failed | BLOCKED
BLOCKED = "blocked"  # skipped because something before it failed
SKIPPED = "skipped"  # skipped for another reason (e.g. a path not taken)
TYPED = "__typed__"  # inputs you typed, as you typed them
STALE = "__stale__"  # made from older data: a block before it ran again on its own since
WITHHELD = "__withheld__"  # outputs that were produced but not saved (passwords, things that cannot be saved)


def _holds_secret(value) -> bool:
    if isinstance(value, SecretStr):
        return True
    if isinstance(value, dict):
        return any(_holds_secret(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_holds_secret(v) for v in value)
    return False


NO_VALUE = object()  # an input you left empty: the block gets no value for it


def input_ports(flow: Flow, registry, only: str) -> dict:
    if only not in flow.blocks:
        raise ValueError(f"there is no block named '{only}'")
    spec = flow.blocks[only]
    cls = registry.get(spec.type)
    return all_ports(cls, block_config(cls, spec))[0] if cls else {}


def _saved(run_dir: Path, block: str) -> dict:
    path = run_dir / block / KEPT_OUTPUTS
    return pickle.loads(path.read_bytes()) if path.exists() else {}


def block_inputs(flow: Flow, registry, home, only: str, run_id: int) -> tuple[dict, dict, dict]:
    """What `only` gets when run on its own from run `run_id`: ({input: value}, {input: why it gets none},
    {input: the text you typed for it}). If it ran on its own then: exactly what it got. Otherwise what
    a full run would give it now, from what the blocks before it hold (e.g. after fixing one of them).
    Raises ValueError when that run's files are gone."""
    input_ports(flow, registry, only)  # a block that exists
    cleaned = f"the files of run #{run_id} were cleaned up (keep_runs)"
    run_dir = home.runs_dir / str(run_id)
    if not run_dir.is_dir():
        raise ValueError(cleaned)
    connected = list(dict.fromkeys(e.dst_port for e in flow.edges if e.dst == only))

    def table(path: Path) -> Table:
        if not path.exists():
            raise ValueError(cleaned)
        return Table(path)

    ran = _saved(run_dir, only).get(STATUS) in ("success", "failed", "cancelled")  # it got its inputs
    if ran and (run_dir / only / KEPT_INPUTS).exists():
        kept = pickle.loads((run_dir / only / KEPT_INPUTS).read_bytes())
        missing = {port: f"{only}.{port} held a password (or something that cannot be saved), so it was not kept"
                   for port in kept.pop(WITHHELD, [])}
        typed = kept.pop(TYPED, {})
        return {port: _map_marks(value, table) for port, value in kept.items()}, missing, typed
    verdict, ports = _Now(flow, registry, run_dir, run_id).inputs(only)
    if verdict in ("blocked", "unknown"):
        return {}, {port: ports for port in connected}, {}
    values, missing = {}, {}
    for port, got in ports.items():
        if isinstance(got, str):
            missing[port] = got
            continue
        delivered = []
        for edge, saved in got:
            if edge.src_port in saved.get(WITHHELD, []):
                missing[port] = (f"{edge.src}.{edge.src_port} held a password (or something that cannot be saved), "
                                 "so it was not kept")
                break
            delivered.append(_map_marks(saved[edge.src_port], table))
        else:  # as the runner hands them over: a list from several edges, else the value
            many = getattr(input_ports(flow, registry, only).get(port), "many", False)
            values[port] = delivered if many or _edges_into(flow, only, port) > 1 else delivered[0]
    return values, missing, {}


def _edges_into(flow: Flow, block: str, port: str) -> int:
    return sum(1 for e in flow.edges if e.dst == block and e.dst_port == port)


class _Now:
    """What a full run would do now, by its rules, from what each block holds in a run's folder.

    A block holds its saved outputs, unless a block before it ran again on its own since (it is
    stale): then a full run would run it again, so what it gives is not known, unless a full run
    would skip it now (e.g. an If before it took the other path).
    """

    def __init__(self, flow, registry, run_dir, run_id):
        self.flow, self.registry, self.run_dir, self.run_id = flow, registry, run_dir, run_id
        self.memo: dict = {}
        starts = _start_blocks(flow, registry)
        # A full run of a flow with start blocks never runs a block connected to none of them (it is off).
        self.on = _taking_part(flow, registry, starts)
        # The start blocks that started the run whose results the folder holds (that run, or the one a re-run
        # carried on from): a block outside their lines, with nothing saved, took no part in it.
        fired = {b for b in starts if STATUS in _saved(run_dir, b)}
        self.line = _line(flow, registry, fired) if fired else None

    def state(self, block: str) -> tuple[str, dict, str]:
        """(ran | failed | blocked | skipped | absent (it took no part) | unknown, saved outputs, why)."""
        if block not in self.memo and self.on is not None and block not in self.on:
            self.memo[block] = ("absent", {}, f"{block} is connected to no start block (it is off)")
        if block not in self.memo:
            saved = _saved(self.run_dir, block)
            if not saved.get(STALE):
                status = saved.get(STATUS)
                kind = {"success": "ran", "failed": "failed", BLOCKED: "blocked", SKIPPED: "skipped"}.get(status, "unknown")
                why = (f"{block} did not run in run #{self.run_id} (it was not reached, e.g. the run was cancelled "
                       "or ran another block on its own): run the whole flow instead" if status is None
                       else f"{block} was {status} in run #{self.run_id}: retry {block} first")
                if status is None and self.line is not None and block not in self.line:
                    kind, why = "absent", f"{block} took no part in run #{self.run_id}"
            else:
                verdict, why = self.inputs(block)
                kind = {"ready": "unknown", "blocked": "blocked", "skipped": "skipped", "unknown": "unknown"}[verdict]
                if verdict == "ready":  # a full run would run it again
                    why = f"{block} still holds what it made from older data: retry {block} first"
            self.memo[block] = (kind, saved, why if isinstance(why, str) else "")
        return self.memo[block]

    def inputs(self, block: str) -> tuple[str, object]:
        """(ready | skipped, {input: [(edge, saved outputs of its source)] or why it gets nothing}), or
        (blocked | unknown, why): the runner's rules for whether a block runs."""
        incoming = [e for e in self.flow.edges
                    if e.dst == block and not (e.dst_port == AFTER and self.state(e.src)[0] == "absent")]
        for edge in incoming:  # nothing downstream of a failure runs; error outputs are the way out
            kind = self.state(edge.src)[0]
            if kind in ("failed", BLOCKED) and edge.src_port != ERROR_PORT:
                return "blocked", (f"{block} would not run: "
                                   + (f"{edge.src}, before it, failed" if kind == "failed" else "something before it failed"))
        for edge in incoming:
            kind, _, why = self.state(edge.src)
            if kind == "unknown":
                return "unknown", why
        in_ports = input_ports(self.flow, self.registry, block)
        ports, ready, after_failure = {}, True, False
        for port in dict.fromkeys(e.dst_port for e in incoming):
            delivered, missing = [], []
            for edge in (e for e in incoming if e.dst_port == port):
                kind, saved, _ = self.state(edge.src)
                if kind in ("ran", "failed") and (edge.src_port in saved or edge.src_port in saved.get(WITHHELD, [])):
                    delivered.append((edge, saved))
                else:
                    missing.append(edge)
            if not delivered or (missing and not getattr(in_ports.get(port), "any_of", False)):
                ready = False
                after_failure |= any(self.state(e.src)[0] in ("failed", BLOCKED) for e in missing)
                ports[port] = f"nothing reached {block}.{port} in run #{self.run_id}"
            else:
                ports[port] = delivered
        if after_failure:
            return "blocked", f"{block} would not run: something before it failed"
        return ("ready" if ready else "skipped"), ports


def blocks_from(flow: Flow, registry, block: str) -> set[str]:
    """What running from `block` on runs: it, and the blocks after it that are on (a block connected to no
    start block is off)."""
    on = _taking_part(flow, registry, _start_blocks(flow, registry))
    return {block} | {b for b in _after(flow, block) if on is None or b in on}


def _from_before(flow: Flow, registry, home, part: set, run_id: int | None, start: str) -> tuple[dict, set, set, set]:
    """For running the blocks in `part` (block `start` and the blocks after it): what the blocks before
    them, which do not run, hold in run `run_id` ({(block, output): value}); which of them failed (or did
    not run because something before them failed); of those, the ones whose failure nothing handles; and
    the ones that took no part in that run. Refuses (ValueError) when what one holds is not current.
    (`start` has its inputs worked out already.)"""
    before = [e for e in flow.edges if e.dst in part and e.src not in part and e.dst != start]
    if run_id is None:
        if before:
            raise ValueError(f"{before[0].dst} needs what {before[0].src} holds: say which run's to use (--inputs-from)")
        return {}, set(), set(), set()
    cleaned = f"the files of run #{run_id} were cleaned up (keep_runs)"
    run_dir = home.runs_dir / str(run_id)
    if not run_dir.is_dir():
        raise ValueError(f"{cleaned}: run the whole flow instead")

    def table(path: Path) -> Table:
        if not path.exists():
            raise ValueError(f"{cleaned}: run the whole flow instead")
        return Table(path)

    now, outputs, blocked, unhandled, absent = _Now(flow, registry, run_dir, run_id), {}, set(), set(), set()
    for edge in before:
        kind, saved, why = now.state(edge.src)
        if kind == "absent":
            absent.add(edge.src)
        if kind == "unknown":
            raise ValueError(f"{why}, or run from it")
        if kind in ("failed", BLOCKED):
            blocked.add(edge.src)  # as in a full run: nothing after it runs (but its error handlers)
            if _unhandled_failure(flow, now, edge.src):
                unhandled.add(edge.src)  # a failure nothing handles: a run it stops has failed
        if kind in ("ran", "failed"):
            if edge.src_port in saved.get(WITHHELD, []):
                raise ValueError(f"{edge.src}.{edge.src_port} held a password (or something that cannot be saved), "
                                 "so it was not kept: run the whole flow instead")
            if edge.src_port in saved:
                outputs[(edge.src, edge.src_port)] = _map_marks(saved[edge.src_port], table)
    return outputs, blocked, unhandled, absent


def _unhandled_failure(flow: Flow, now: "_Now", block: str) -> bool:
    """`block` failed, or did not run because a block before it failed: and nothing handles that failure
    (none of the failed blocks behind it has an error handler that is on)."""
    def handled(failed: str) -> bool:
        return any(e.src == failed and e.src_port == ERROR_PORT and (now.on is None or e.dst in now.on)
                   for e in flow.edges)

    if now.state(block)[0] == "failed":
        return not handled(block)
    seen, todo = {block}, [block]
    while todo:  # back through what stopped it: the error output of a failed block is the way out, but
        current = todo.pop()  # a block that did not run gave no error either (as in the runner's rules)
        for edge in flow.edges:
            if (edge.dst == current and edge.src not in seen
                    and (edge.src_port != ERROR_PORT or now.state(edge.src)[0] == BLOCKED)):
                seen.add(edge.src)
                kind = now.state(edge.src)[0]
                if kind == "failed" and not handled(edge.src):
                    return True
                if kind == BLOCKED:
                    todo.append(edge.src)
    return False


def _only_inputs(flow: Flow, registry, home, only: str, run_id: int | None, given: dict,
                 texts: dict) -> tuple[dict, dict]:
    """The inputs for running just `only`: the ones you give, the rest as they reached it in run `run_id`;
    and the ones you typed (now, or for the inputs it keeps from that run), as you typed them."""
    in_ports = input_ports(flow, registry, only)
    unknown = sorted(set(given) - set(in_ports))
    if unknown:
        raise ValueError(f"{only} has no input named '{unknown[0]}'")
    try:
        values, missing, typed = (block_inputs(flow, registry, home, only, run_id) if run_id is not None
                                  else ({}, {}, {}))
    except ValueError as e:
        raise ValueError(f"{e}: run the whole flow instead, or choose its inputs") from None
    if run_id is None and not given and any(e.dst == only and e.dst_port != AFTER for e in flow.edges):
        raise ValueError("say which run's inputs to use (--inputs-from), or give them (--input)")
    # Inputs it cannot have as they were then (and you did not give) are refused. With values you
    # give, a block that did not run then may still lack its `after` (it only sets the order).
    unresolved = [why for port, why in missing.items() if port not in given and not (given and port == AFTER)]
    if unresolved:
        raise ValueError(f"{unresolved[0]}: run the whole flow instead, or choose its inputs")
    values = {k: v for k, v in {**values, **given}.items() if v is not NO_VALUE}
    needed = [name for name, port in in_ports.items() if port.required and name not in values]
    if needed:
        raise ValueError(f"{only}.{needed[0]} needs a value")
    typed = {k: t for k, t in typed.items() if k not in given} | {k: texts[k] for k in given if texts[k].strip()}
    return values, typed


def _table_file(path) -> "pl.DataFrame | pl.LazyFrame":
    """table('file.csv') or table('file.parquet') in an input value you type."""
    path = Path(path)
    if path.suffix.lower() == ".parquet":
        return pl.scan_parquet(path)  # copied into the run, so later changes to your file do not matter
    if path.suffix.lower() == ".csv":
        return pl.read_csv(path)
    raise ValueError(f"table() reads .csv and .parquet files, not {path.name}")


def _evaluate_inputs(texts: dict, params: dict) -> dict:
    """Input values you type (e.g. to test a block) are Python expressions; empty means no value."""
    namespace = {"pl": pl, "datetime": dt, "Decimal": decimal.Decimal, "Path": Path, "table": _table_file,
                 "params": params}
    values = {}
    for name, text in texts.items():
        if not text.strip():
            values[name] = NO_VALUE
            continue
        try:
            values[name] = eval(compile(text, f"<the value for {name}>", "eval"), dict(namespace))
        except Exception as e:
            raise ValueError(f"the value for {name} ({text}): {type(e).__name__}: {e}") from None
    return values


def _after(flow: Flow, block: str) -> set[str]:
    """The blocks that come after `block` (directly or through others)."""
    found, todo = set(), [block]
    while todo:
        for edge in flow.edges:
            if edge.src == todo[-1] and edge.dst not in found:
                found.add(edge.dst)
                todo.insert(0, edge.dst)
        todo.pop()
    return found


def _tables_to_marks(value):
    """Tables (also inside lists and dicts) as {"__table__": path}, to save them."""
    if isinstance(value, Table):
        return {"__table__": str(value.path)}
    if isinstance(value, (list, tuple)):
        return type(value)(_tables_to_marks(v) for v in value)
    if isinstance(value, dict):
        return {k: _tables_to_marks(v) for k, v in value.items()}
    return value


def _map_marks(value, fn):
    """`value` with fn(path) applied to each saved table mark."""
    if isinstance(value, dict) and set(value) == {"__table__"}:
        return fn(Path(value["__table__"]))
    if isinstance(value, (list, tuple)):
        return type(value)(_map_marks(v, fn) for v in value)
    if isinstance(value, dict):
        return {k: _map_marks(v, fn) for k, v in value.items()}
    return value


def _carry_over(old: Path, new: Path, after_only: set[str]):
    """Copy the saved outputs (and inputs) of every block of an earlier run into a new run's folder.
    Table files are hard-linked (no extra space; they outlive the old run's folder), or copied.
    Saved inputs of blocks after the one that runs are left behind: its new outputs replace them."""
    kept_files = [*old.glob(f"*/{KEPT_OUTPUTS}"),
                  *(f for f in old.glob(f"*/{KEPT_INPUTS}") if f.parent.name not in after_only)]
    for kept_file in sorted(kept_files):
        block, copies = kept_file.parent.name, iter(range(1_000_000))

        def relink(source: Path):
            if not source.exists():
                return {"__table__": str(source)}  # already cleaned up: using it is refused with a clear message
            target = new / block / "kept" / kept_file.stem / f"{next(copies)}.parquet"
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(source, target)
            except OSError:
                shutil.copy2(source, target)
            return {"__table__": str(target)}

        kept = _map_marks(pickle.loads(kept_file.read_bytes()), relink)
        if block in after_only and kept_file.name == KEPT_OUTPUTS:
            kept[STALE] = True  # it made these from what the block that runs now made before
        (new / block).mkdir(parents=True, exist_ok=True)
        (new / block / kept_file.name).write_bytes(pickle.dumps(kept))


def _taking_part(flow: Flow, registry, fired: set[str]) -> set[str] | None:
    """The blocks a run takes part in. In a flow with start blocks: the blocks after the ones that started
    it; the blocks whose data those need (e.g. a query feeding one of them; not through an error or a
    run-after connection); and the error handlers of all of these, with what comes after them. A block
    connected to no start block takes no part: that is how to switch it off.
    None: a flow without start blocks runs all its blocks."""
    if not any((cls := registry.get(spec.type)) is not None and cls.is_trigger for spec in flow.blocks.values()):
        return None
    part: set[str] = set()

    def with_what_follows(blocks):
        todo = [b for b in blocks if b not in part]
        part.update(todo)
        while todo:
            block = todo.pop()
            for edge in flow.edges:
                if edge.src == block and edge.dst not in part:
                    part.add(edge.dst)
                    todo.append(edge.dst)

    with_what_follows(fired)
    grown = True
    while grown:
        grown = False
        for edge in flow.edges:
            data = edge.src_port not in (ERROR_PORT, DONE) and edge.dst_port != AFTER
            if data and edge.dst in part and edge.src not in part:  # its data is needed
                part.add(edge.src)
                grown = True
            elif edge.src_port == ERROR_PORT and edge.src in part and edge.dst not in part:  # its error handler
                with_what_follows([edge.dst])
                grown = True
    return part


def _line(flow: Flow, registry, fired: set[str]) -> set[str]:
    """The blocks a run started by the start blocks `fired` takes part in (their lines; the other start
    blocks take no part). Every block, in a flow without start blocks."""
    part = _taking_part(flow, registry, fired)
    return set(flow.blocks) if part is None else part - (_start_blocks(flow, registry) - fired)


def lines(flow: Flow, registry) -> dict[str, set[str]]:
    """Each start block's line, in the flow's order: the blocks the runs it starts take part in."""
    starts = _start_blocks(flow, registry)
    return {b: _line(flow, registry, {b}) for b in flow.blocks if b in starts}


def _start_blocks(flow: Flow, registry) -> set[str]:
    return {b for b, s in flow.blocks.items() if (cls := registry.get(s.type)) is not None and cls.is_trigger}


def _fired_triggers(flow: Flow, registry, trigger: str | None) -> set[str]:
    """Start blocks that fire: the Schedule block of a scheduled run; Manual Start blocks of a manual
    run, or every start block when the flow has no Manual Start (so pressing Run always runs it)."""
    starts = _start_blocks(flow, registry)
    if trigger is not None:
        return {trigger} & starts
    manual = {b for b in starts if flow.blocks[b].type == "trigger.manual"}
    return manual or starts


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
