"""Runs a flow: blocks in topological order, with retries, timeouts, error ports and cancellation; a Splitter's lines
at the same time or one after another; the blocks inside a Loop once per item."""

from __future__ import annotations

import contextlib
import copy
import datetime as dt
import decimal
import http.cookiejar
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
from .block import AFTER, DONE, all_ports, fields, has_done_output, setting_label
from .flow import (API_REQUEST, AS_FINISHED, AT_ONCE, ERROR_PORT, INDEX, ITEM, ITEMS, LINES, LOOP, MERGER, ONE_AFTER_ANOTHER,
                   RESULT, RESULTS, RUNS_AGAIN, SPLITTER, Flow, FlowError, _start_blocks, _taking_part, at_level, block_config, containers,
                   from_inside, line_runs, lines_run, order, per_line, per_line_of, run_clock, splitter_lines,
                   validate)
from .config import REVEALED, before_now, kept_runs, runs_kept_since
from .history import History
from .table import Table

# How long a block gets to stop on its own after a timeout or cancel before it is abandoned.
STOP_GRACE_SECONDS = 2.0
# How long none of a run's threads may be at work, each waiting for something, before the run cannot go on (_watch):
# well over the 0.1 s within which a waiting thread looks again whether what it waits for has come.
STUCK_SECONDS = 0.5
# Held while a Session's cookie jar is made (and filled from the run a run carries on from): lines running at the same
# time get the same one (_Runner._session_cookies).
_SESSIONS = threading.Lock()


class Cancelled(Exception):
    pass


class Failed(Exception):
    """A failure the block expects, which its message says all about (e.g. no answer was given): told by that
    message alone, without the Python traceback an unexpected error (a bug) keeps. Otherwise an ordinary failure:
    retried, and out the block's error output, which also carries `carried` (e.g. an API Request's status, headers
    and body)."""

    def __init__(self, message: str, **carried):
        super().__init__(message)
        self.carried = carried


def _told(error: BaseException) -> str:
    """A block's error as its failure is told: by its message alone when the block expects it (Failed) or attaches
    its own report (Python Code), otherwise with the kind of error first (e.g. KeyError: 'region')."""
    if isinstance(error, Failed) or getattr(error, "details", None):
        return str(error)
    return f"{type(error).__name__}: {error}"


class SecretStr(str):
    """A string that never shows its value in logs, summaries or run history."""

    def __new__(cls, value):
        REVEALED.add(str.__str__(value))
        return super().__new__(cls, value)

    def __repr__(self):
        return "'***'"


HIDDEN: set[str] = set()  # every HiddenStr's value this process has seen


class HiddenStr(SecretStr):
    """A SecretStr that is no password, such as a cookie a server set: shown as *** like a secret, but kept with the
    run's outputs (as a Session's cookies are), so a block after it can be run again."""

    def __new__(cls, value):
        HIDDEN.add(str.__str__(value))
        return str.__new__(cls, value)


_forms: tuple = ((0, 0), [])  # secret_forms' last list, and the sizes of REVEALED and HIDDEN it was made for


def secret_forms() -> list[str]:
    """Every secret and HiddenStr seen in this process (of 4 characters or more), also as repr() and JSON write it
    inside a quoted text (a \\ or quote escaped, ä as \\u00e4); longest first. Made again only when one was added
    (both sets only grow): a Loop whose calls each get a new cookie would otherwise make it for every text logged."""
    import json

    global _forms
    sizes = (len(REVEALED), len(HIDDEN))  # before reading them: one added meanwhile makes it again next time
    if _forms[0] != sizes:
        forms = {form for s in list(REVEALED | HIDDEN) if len(s) >= 4
                 for form in (s, repr(s)[1:-1], repr(s + "'\"")[1:-4], json.dumps(s)[1:-1],
                              json.dumps(s, ensure_ascii=False)[1:-1])}
        _forms = (sizes, sorted(forms, key=len, reverse=True))
    return _forms[1]


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
                 attempt_cancel, answers=None, registry=None, cookies=None, run_id=None, flow_path=None):
        self.block_id = block_id
        # The run (its number in the run history) and the flow file it runs (as the run history keeps its path).
        self.run_id, self.flow_path = run_id, flow_path
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
        # An API Request inside a Session: the Session's cookie jar (http.cookiejar.CookieJar, for urllib's
        # HTTPCookieProcessor), whose cookies its requests send and to which the responses add theirs; else None.
        self.cookies = cookies
        self._saw_stop = False  # it found out it was told to stop (so what it returns may be part of its work)

    def log(self, message: str, level: str = "INFO", show: str | None = None):
        """`show`: how the editor's Log draws the message, "image" (it is a picture's path) or "markdown"."""
        event = {"event": "log", "block": self.block_id, "level": level, "message": str(message)}
        if show is not None:
            event["show"] = show
        self._emit(event)

    def warn(self, message: str):
        self.log(message, "WARNING")

    def sending_query(self, title: str, sql: str, names: list[str], values: dict):
        """Keep in the run history the SQL the block is sending (its :names made ?s by params.to_qmark) and the value
        bound to each ?, in order, as an SQL literal: a secret only as <hidden: secret NAME> (NAME its bind
        parameter's), never its text. For Run history's Copy executed query."""
        bound = [f"<hidden: secret {name}>" if isinstance(value, SecretStr) else P.sql_literal(value)
                 for name in names for value in (values[name] if isinstance(values[name], list) else [values[name]])]
        self._emit({"event": "query", "block": self.block_id, "title": title, "sql": sql, "values": bound})

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
        """Ask the person running the flow and wait for the answer (None if they decline). Stopped waiting (the run
        stopped, or the block's Timeout ran out): the Log says why, and the editor closes the question's box."""
        if self._answers is None:
            raise RuntimeError("this run cannot ask questions")
        ask_id = uuid.uuid4().hex
        self._emit({"event": "ask", "block": self.block_id, "ask_id": ask_id, "prompt": prompt,
                    "kind": kind, "options": list(options or []), "default": default})
        try:
            return self._answers.wait(ask_id, self)
        except Cancelled:
            why = "the run was stopped" if self._run_cancel.is_set() else "the block's Timeout ran out"
            self.warn(f"question closed without an answer: {why}")
            self._emit({"event": "ask_closed", "block": self.block_id, "ask_id": ask_id})
            raise

    @property
    def cancel_event(self) -> threading.Event:
        """Set when the whole run is cancelled; pass it to runs started from this block."""
        return self._run_cancel

    @property
    def cancelled(self) -> bool:
        if self._run_cancel.is_set() or self._attempt_cancel.is_set():
            self._saw_stop = True
            return True
        return False

    def check_cancelled(self):
        if self.cancelled:
            raise Cancelled()

    def waiting_until(self, until: dt.datetime | None):
        """Tell the editor this block waits until `until` (local time; None: it no longer waits): it shows the time
        left on the block. Not kept in the run history; `taskloom run` prints nothing for it."""
        self._emit({"event": "waiting", "block": self.block_id,
                    "until": until and until.isoformat(timespec="milliseconds")})

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
    """A readable look at any value: a table's rows (over 10: its first 5, a row of …, its last 5), text, or a
    list/dict as JSON."""
    import json

    import polars as pl

    if isinstance(value, Table):
        total, lazy = value.num_rows, value.scan()
        # Over 10 rows: the first 6 and the last 5, which Polars shows as the first 5, a row of …, the last 5.
        rows = lazy.head(10).collect() if total <= 10 else pl.concat([lazy.head(6).collect(),
                                                                      lazy.slice(total - 5).collect()])
        for secret in secret_forms():  # scrub the text cells, then cut them (a cut secret is no longer found)
            rows = rows.with_columns(pl.col(pl.String).str.replace_all(secret, "***", literal=True))
        with pl.Config(tbl_rows=10, tbl_cols=20, fmt_str_lengths=60, tbl_width_chars=200,
                       tbl_hide_dataframe_shape=True):  # the summary says how many rows it has
            return f"{value.summary()}\n{rows}"
    if isinstance(value, SecretStr):
        return "***"
    if isinstance(value, (dict, list, tuple)):
        return redact(json.dumps(_masked(value), indent=2, default=str, ensure_ascii=False))[:limit]  # scrub, then cut
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


def finished_text(event: dict) -> str:
    """A finished block's line (after its name): its status, its summary or error, and the full path of the file
    of each table it produced, to copy. A block inside a Loop, over its items: how they ended (✓ 4 ✕ 1), then that
    for the last item to end."""
    detail = event.get("summary") or event.get("error")
    items = event.get("items")
    status = f"{items['ended']} · the last, {items['last']}: {items['status']}" if items else event["status"]
    return " · ".join([status, *([detail] if detail else []), *(event.get("tables") or {}).values()])


def _summary(produced: dict) -> str:
    parts = []
    for port, value in produced.items():
        text = value.summary() if isinstance(value, Table) else redact(repr(value))  # scrub, then cut
        parts.append(f"{port}: {text[:200]}")
    return "; ".join(parts)


def run_flow(flow: Flow, registry, home, overrides: dict | None = None, sinks=(), cancel: threading.Event | None = None,
             answers=None, as_of: dt.datetime | None = None, trigger: str | None = None,
             only: str | None = None, inputs_from: int | None = None, given: dict | None = None,
             run_from: str | None = None, before_start=None, scheduled: bool = False) -> RunResult:
    """Run a flow. `answers` (with a wait(ask_id, ctx) method) lets blocks ask the user questions.

    `trigger` is the Schedule block that started a scheduled run; None means a manual run.
    `only` runs just that block: with the inputs it got in run `inputs_from`, and the ones in
    `given` ({input: Python expression}; empty means no value) instead.
    `run_from` runs that block (with the inputs it got in run `inputs_from`) and every block after it;
    the blocks before them are not run again: what they held in run `inputs_from` is used.
    `before_start` is called once the flow is loaded and checked, just before its run is recorded and begins
    (`taskloom run --start-at` waits there for its time). `scheduled`: the scheduler service started it (its run
    history says so: see History.start_run).
    """
    fired = _fired_triggers(flow, registry, trigger)
    part = run_part(flow, registry, trigger, only, run_from)
    # A problem of a block taking no part in it does not stop it: its Log warns about it.
    errors, warnings = validate(flow, registry, home, part)
    if errors:
        raise FlowError(errors)
    now, funcs = run_clock(flow, home, as_of)
    values = P.resolve(flow.params, overrides or {}, funcs)
    texts = dict(given or {})  # as you typed them: shown again next time, and read again then
    start = only or run_from
    if start in flow.blocks and (around := containers(flow, start)):
        raise ValueError(f"{start} is inside the loop {around[-1]}: running a block inside a loop on its own is not "
                         f"offered yet; run from {around[-1]} instead (it runs again the items that failed or did not "
                         "run)")
    if start in flow.blocks and (merger := per_line_of(per_line(flow, registry), start)):
        raise ValueError(f"{start} runs once per line of the Merger {merger} (each line as it finishes): running it "
                         f"on its own is not offered yet; run from {merger} instead (it runs again the lines' runs "
                         "that failed or did not run)")
    # Running from a block, or only a Loop (it runs the blocks inside it), the blocks that run take what the blocks
    # before them, which do not run again, held in run inputs_from.
    takes_before = bool(run_from or (only and part - {only}))
    if start is not None:  # refuse before a run is recorded
        given = _evaluate_inputs(texts, values)
        _only_inputs(flow, registry, home, start, inputs_from, given, texts)
        if takes_before:
            _from_before(flow, registry, home, part, inputs_from, start)
    started_by = (f"only {only}" if only else f"from {run_from}" if run_from
                  else ", ".join(b for b in flow.blocks if b in fired))
    # The start blocks it counts for (a re-run: those whose lines hold its block), and how the flow's start blocks
    # keep their runs.
    counts_for = ([b for b, line in lines(flow, registry).items() if start in line] if start is not None
                  else [b for b in flow.blocks if b in fired])
    keeps = {b: (flow.blocks[b].config.get("keep"), bool(flow.blocks[b].config.get("keep_failed")))
             for b in flow.blocks if b in _start_blocks(flow, registry)}
    cancel = cancel or threading.Event()
    if before_start is not None:
        before_start()

    history = History(home.history_db)
    run_id = history.start_run(flow.name, str(flow.path), values, _now(), started_by, counts_for, keeps, flow.version,
                               scheduled)
    all_sinks = (history, *sinks)
    running: dict = {}  # block -> attempt, for the blocks started and not finished
    # Lines run at the same time tell their events one at a time, each whole and in the order they happen.
    telling, told_end = threading.Lock(), []

    def emit(event: dict):
        with telling:
            if told_end:  # (a line still going as the run ended, e.g. on a second Ctrl+C)
                return
            event = redact({"event": event.pop("event"), "run_id": run_id, "ts": _now(), **event})
            if event["event"] == "block_started":
                running[event["block"]] = event["attempt"]
            for sink in all_sinks:
                sink(event)
            if event["event"] == "block_finished":  # once every sink has it
                running.pop(event["block"], None)
            if event["event"] == "run_finished":
                told_end.append(True)

    try:
        emit({"event": "run_started", "flow": flow.name, "params": {k: str(v) for k, v in values.items()},
              "blocks": sorted(part), "started_by": started_by})
        for message in registry.warnings + warnings:
            emit({"event": "log", "level": "WARNING", "message": message})
        runner = _Runner(flow, registry, home, values, now, funcs, emit, cancel, home.runs_dir / str(run_id), answers)
        runner.taking_part, runner.outside, runner.inputs_from = part, set(flow.blocks) - part, inputs_from
        runner.run_id = run_id
        if inputs_from is not None:
            runner.earlier_ends = {b: r["finished"] or "" for b, r in history.blocks(inputs_from).items()}
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
                runner.only_inputs, runner.only_typed, runner.only_came = _only_inputs(
                    flow, registry, home, start, source, given, texts)
                if takes_before:
                    runner.outputs, runner.blocked, runner.before_unhandled, runner.outside = _from_before(
                        flow, registry, home, part, source, start)
            status = runner.run()
        except BaseException:
            # Never leave the run, or a block of it, recorded as "running" (e.g. a forced second Ctrl+C).
            status = "cancelled" if cancel.is_set() else "failed"
            with telling:
                still = list(running.items())
            for block, attempt in still:
                emit({"event": "block_finished", "block": block, "status": status, "attempts": attempt})
            emit({"event": "run_finished", "status": status})
            raise
        emit({"event": "run_finished", "status": status})
        _apply_retention(home, history)
    finally:
        history.close()
    return RunResult(run_id, status, runner.outputs)


class _Watch:
    """Whether a run can still go on (see _Runner._watch): how many of its threads are at work, that is not waiting
    for a block before theirs, a line's turn, a Loop's items or the lines to end (the one running the flow's own blocks
    to start with), and how often one went back to work; its runners (the flow's own blocks, each item of a Loop
    running)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.busy, self.moves = 1, 0
        self.runners: list = []
        self.over = threading.Event()  # the run has ended
        self.stuck = False  # it could not go on: it fails

    @contextlib.contextmanager
    def idle(self):
        """While the thread waits."""
        with self.lock:
            self.busy -= 1
        try:
            yield
        finally:
            with self.lock:
                self.busy += 1
                self.moves += 1

    def at_work(self, target):
        """`target`, for a thread about to start: it is at work until it ends."""
        with self.lock:
            self.busy += 1

        def counted(*args):
            try:
                target(*args)
            finally:
                with self.lock:
                    self.busy -= 1
        return counted


class _Runner:
    def __init__(self, flow, registry, home, values, now, funcs, emit, cancel, workspace, answers):
        self.flow, self.registry, self.home, self.answers = flow, registry, home, answers
        self.values, self.now, self.funcs = values, now, funcs
        self.emit, self.cancel, self.workspace = emit, cancel, workspace
        self.outputs: dict = {}
        self.blocked: set[str] = set()  # failed, or skipped because something before them failed
        self.ran: set[str] = set()  # the blocks that ran (not skipped)
        # The failures this run answers for: its blocks that failed, and the failures before them that stopped
        # one of its blocks (see before_unhandled).
        self.failures: set[str] = set()
        self.errors: dict[str, str] = {}  # the blocks that failed in this run: what their failure says
        self.jars: dict = {}  # the cookie jar of each Session at its level (see _session_cookies)

    only: str | None = None  # run just this block, with only_inputs
    only_inputs: dict = {}
    only_typed: dict = {}  # the ones you typed, as you typed them
    only_came: dict = {}  # the others: the connections they came by (see _came)
    brought: dict = {}  # what each of those connections brought: {input: {(block, output): value}}
    taking_part: set = set()  # the blocks this run takes part in: the only ones it runs and reports
    run_from: str | None = None  # run from this block on (with only_inputs): taking_part is it and the blocks after it
    outside: set = set()  # blocks taking no part in it (nor in the run it carries on from): see _gather
    before_unhandled: dict = {}  # blocks before them: {block: the failures behind it no block that ran handled}
    inputs_from: int | None = None  # the run it carries on from
    earlier_ends: dict = {}  # when each block finished in that run (its run history), for a Merger's Runs again in
    # Running the blocks inside a Loop for one of its items, or the blocks after a Merger set to each line as it
    # finishes for one of its lines (see _item_runner): the Loop they are inside (None: in none), and the run's tag in
    # the Log, e.g. [item 2/5] or [line load_sales]. None and "": the flow's own blocks.
    loop: str | None = None
    item: str = ""
    run_id: int | None = None  # the run's number in the run history
    parent: "_Runner | None" = None  # the runner whose item or line this one runs (_item_runner)
    ordered: list | None = None  # the flow's blocks in order (order()), worked out once for the run
    sections: dict | None = None  # the blocks each Merger set to each line as it finishes runs (per_line), likewise

    def run(self) -> str:
        """The blocks outside a Splitter's lines run here one at a time, in the flow's order (each once the blocks
        before it are done with; see _run_blocks), as in a flow without one; each line runs its own blocks so, in a
        thread of its own, once its Splitter is done with, as the Splitter says (_start_lines). Only the blocks of its
        level: the flow's own, or (running an item of a Loop) those directly inside the Loop; a Loop runs the blocks
        inside it (_run_loop), a Merger set to each line as it finishes the blocks after it (_run_per_line). A run that
        can no longer go on fails (_watch)."""
        self.lines = {s: its for s, its in splitter_lines(self.flow).items() if self.flow.blocks[s].inside == self.loop}
        self.runs = line_runs(self.flow, self.taking_part, self.lines)
        self.ordered = self.ordered or order(self.flow, self.taking_part)
        self.sections = per_line(self.flow, self.registry) if self.sections is None else self.sections
        # The Mergers set to each line as it finishes at its level, with the blocks after them they run once per line:
        # not its own (one in the runs of another runs in those).
        mine = {m: blocks & self.taking_part for m, blocks in self.sections.items()
                if m in self.taking_part and self.flow.blocks[m].inside == self.loop}
        self.each_line = {m: blocks for m, blocks in mine.items() if not any(m in other for other in mine.values())}
        owner = {b: m for m, blocks in self.each_line.items() for b in blocks}
        self.units: dict = {}  # None (outside the lines) or (Splitter, line index): its blocks, in the flow's order
        for block_id in [b for b in self.ordered if b in self.taking_part and self.flow.blocks[b].inside == self.loop
                         and b not in owner]:
            self.units.setdefault(self.runs.get(block_id), []).append(block_id)

        def level(block: str) -> str | None:
            at = at_level(self.flow, block, self.loop)
            return owner.get(at, at)

        # Each block's blocks before it that run, at its level (a block inside a Loop counts as the Loop; one run once
        # per line, as its Merger).
        pairs = [(level(e.src), level(e.dst)) for e in self.flow.edges
                 if e.src in self.flow.blocks and e.dst in self.flow.blocks]
        self.before = {b: {a for a, d in pairs if d == b and a not in (None, b) and a in self.taking_part}
                       for unit in self.units.values() for b in unit}
        for merger in self.each_line:  # (it starts before its lines end: they arrive as each finishes)
            self.before[merger] -= {e.src for e in self.flow.edges if e.dst == merger and e.dst_port == LINES}
        # With lines, or for an item of a Loop or a line of a Merger (they may run at the same time), each block gets
        # its own copy of what it receives: no line, item (nor a block running meanwhile) can change another's data.
        self.copies = bool(self.item) or any(unit is not None for unit in self.units)
        self.settled: set[str] = set()  # the blocks done with: ran, skipped or cancelled
        self.ended: list[str] = []  # the same, in the order they were done with
        self.changed = threading.Condition()
        self.threads: list[threading.Thread] = []
        self.crashed: list[BaseException] = []  # a line's thread ended with an error of Taskloom's own
        self.turns: dict = {}  # each line handed to _start_lines, (Splitter, line index): whether it has started
        self.ending: dict[str, str] = {}  # the blocks that wait for what can never come: what for (_end_stuck)
        if not self.item:  # (the flow's own blocks)
            self.watch = _Watch()
            threading.Thread(target=self._watch, daemon=True, name="watch").start()
        with self.watch.lock:
            self.watch.runners.append(self)
        try:
            for splitter in self.lines:
                if splitter not in self.taking_part:  # (e.g. a run from a block of one of its lines)
                    self._start_lines(splitter)
            self._run_blocks(self.units.get(None, []))
            with self.watch.idle():
                while pending := [t for t in list(self.threads) if t.is_alive()]:
                    pending[0].join(0.1)  # (a Ctrl+C still gets through)
        finally:
            with self.watch.lock:
                self.watch.runners.remove(self)
            if not self.item:
                self.watch.over.set()
        if self.crashed:
            raise self.crashed[0]
        if self.cancel.is_set():
            return "cancelled"
        # One that could not go on fails, even with the failures of the blocks that waited handled.
        stuck = self.watch.stuck if not self.item else self.ending
        return "failed" if self.unhandled() or stuck else "success"

    def unhandled(self) -> list[str]:
        """The failures it answers for that are not handled, in the flow's order. A failure is handled when its error
        reached a block that ran: an error handler that was skipped (e.g. it also needs the failed block's data)
        handles nothing."""
        handled = {e.src for e in self.flow.edges if e.src_port == ERROR_PORT and e.dst in self.ran}
        return [b for b in self.flow.blocks if b in self.failures - handled]

    def _run_blocks(self, blocks: list[str]):
        """Run these blocks one after another, in the flow's order, each once the blocks before it are done with; while
        one waits (for what a line makes), the first after it that need not wait runs meanwhile (a line may be waiting
        for that one). After a Splitter, its lines start. (A Loop also waits for what the blocks inside it take from
        outside it, a Merger set to each line as it finishes for what the blocks after it take from outside them; it
        runs in a thread of its own, its lines arriving as the blocks that need not wait for it run.) One that waits
        for what can never come fails so (_end_stuck)."""
        todo = list(blocks)
        while todo:
            with self.changed, self.watch.idle():
                while not (ready := [b for b in todo if self.before[b] <= self.settled or b in self.ending]) \
                        and not self.cancel.is_set() and not self.crashed:
                    self.changed.wait(0.05)
            if self.crashed:
                return
            block_id = (ready or todo)[0]  # (Stop: each one left ends as cancelled, in order)
            todo.remove(block_id)
            if block_id in self.each_line:
                self._thread(self._settle, block_id)
            else:
                self._settle(block_id)
            if block_id in self.lines:
                self._start_lines(block_id)

    def _settle(self, block_id: str):
        """Run the block; then it is done with."""
        self._run_one(block_id)
        with self.changed:
            self.settled.add(block_id)
            self.ended.append(block_id)
            self.changed.notify_all()

    def _start_lines(self, splitter: str):
        """Run the Splitter's lines that take part in the run: at the same time (at most At most at once of them, the
        next one starting as soon as one ends, in line order) or one after another. A Splitter that succeeded says
        so in the Log."""
        mode, at_most = lines_run(self.flow, self.registry, splitter)
        if splitter in self.ran and (splitter, LINES) in self.outputs:
            names = [first for first, blocks in self.lines[splitter] if blocks & self.taking_part]
            said = f"{len(names)} line{'s' if len(names) != 1 else ''} {mode}"
            if not names:  # (Run only this block on it, or no line connected to it)
                said = "no line runs" + (f": only {splitter} runs" if splitter == self.only else "")
            elif mode == ONE_AFTER_ANOTHER:
                said += ": " + ", ".join(f"{number} {name}" for number, name in enumerate(names, 1))
            else:
                said += (f", at most {at_most} at once" if at_most is not None else "") + ": " + ", ".join(names)
            self.emit({"event": "log", "block": splitter, "level": "INFO", "message": said})
        lines = [i for i in range(len(self.lines[splitter])) if (splitter, i) in self.units]
        if not lines:  # (one line: it runs with the blocks around it, see line_runs)
            return
        slots = threading.Semaphore(1 if mode == ONE_AFTER_ANOTHER else at_most or len(lines))
        with self.changed:
            self.turns.update({(splitter, i): False for i in lines})

        def line(i):
            try:
                self._run_blocks(self.units[(splitter, i)])
            finally:
                slots.release()

        def start():
            for i in lines:
                with self.watch.idle():
                    while not slots.acquire(timeout=0.05):
                        if self.crashed:
                            return
                with self.changed:
                    self.turns[(splitter, i)] = True
                self._thread(line, i)

        self._thread(start)

    def _thread(self, target, *args):
        def guarded():
            try:
                target(*args)
            except BaseException as e:  # a bug of Taskloom's: the run fails with it, nothing waits for it forever
                self.crashed.append(e)
                with self.changed:
                    self.changed.notify_all()

        thread = threading.Thread(target=self.watch.at_work(guarded), daemon=True, name="line")
        self.threads.append(thread)
        thread.start()

    def _watch(self):
        """While the run goes on: once none of its threads has been at work for STUCK_SECONDS, each waiting for
        something (a block before its own, a line's turn, a Loop's items, the lines to end) that none of them brought,
        nothing can bring it any more: the run cannot go on (_end_stuck). A block running, however long (also one
        waiting for an answer, or to try again), is at work."""
        quiet, seen = 0, None
        while not self.watch.over.wait(0.1):
            with self.watch.lock:
                now = (self.watch.busy, self.watch.moves)
            quiet, seen = (quiet + 1 if now == seen and now[0] == 0 else 0), now
            if quiet * 0.1 >= STUCK_SECONDS:
                self._end_stuck()
                quiet = 0

    def _end_stuck(self):
        """The run cannot go on: in each of its runners (the flow's own blocks, each item of a Loop running) the Log
        says which blocks of its lines wait for what, and they fail so, the blocks after them ending as after any
        failure; the run fails."""
        with self.watch.lock:
            runners = list(self.watch.runners)
        for runner in runners:
            if (waiting := runner._waiting()) and not self.cancel.is_set():
                self.watch.stuck = True
                runner.emit({"event": "log", "level": "ERROR", "message": "the run cannot go on: " + ", ".join(
                    f"{block} waits for {what}" for block, what in waiting.items())})
                with runner.changed:
                    runner.ending.update(waiting)
                    runner.changed.notify_all()

    def _waiting(self) -> dict[str, str]:
        """The blocks of its lines that wait, line by line, and what for: in a line under way, each that waits for
        blocks outside it (not for one before it in its line); in a line yet to start, its first blocks, for its
        turn. With none waiting in the lines, the blocks outside them that wait (e.g. for each other)."""
        found = {}
        with self.changed:
            for (splitter, i), started in self.turns.items():
                left = [b for b in self.units[(splitter, i)] if b not in self.settled]
                for b in left:
                    waits = self.before[b] - self.settled
                    if b in self.ending or waits & set(left):
                        continue
                    if not started:
                        mode, at_most = lines_run(self.flow, self.registry, splitter)
                        how = ("its lines one after another" if mode == ONE_AFTER_ANOTHER
                               else f"at most {at_most} of its lines at once")
                        found[b] = f"its turn (line {i + 1} of {splitter}, which runs {how})"
                    elif waits:
                        found[b] = " and ".join(
                            f"{a} (line {self.runs[a][1] + 1} of {self.runs[a][0]})" if a in self.runs else a
                            for a in self.ordered if a in waits)
            if not found:
                for b in self.units.get(None, []):
                    if b not in self.settled and b not in self.ending and (waits := self.before[b] - self.settled):
                        found[b] = " and ".join(a for a in self.ordered if a in waits)
        return found

    def _run_loop(self, spec, inputs) -> tuple[str, dict, list | None]:
        """Run a Loop: the blocks inside it once for every item arriving at its items (a table: each row, as a dict of
        its columns), as a flow of their own (_item_runner), At the same time items at once, the next starting as soon
        as one ends, in item order. Once an item fails (or a block of it fails with nothing at its error output, which
        fails it) no new one starts; the items running end, then the Loop fails, listing the failed items. Running from the Loop (or only it), the items that succeeded in the run this one
        carries on from keep the results they gave there and are not run again. Returns how it ended, its outputs, and
        each item's (item, status, result), kept with its outputs for that (None: it could not start)."""
        loop, cls = spec.id, self.registry.get(spec.type)
        try:
            connected = {e.dst_port for e in self.flow.edges if e.dst == loop and not from_inside(self.flow, e)}
            at_once = _filled(cls, block_config(cls, spec), self.values, inputs, {ITEMS}, connected)[AT_ONCE]
            items = _items_of(inputs.get(ITEMS))
        except Exception as e:
            return (*self._fail(spec, 0, e, inputs), None)
        n, started = len(items), time.monotonic()
        kept = self._earlier_items(loop, items) if loop in (self.only, self.run_from) else {}
        records: list = [(items[k], "success", kept[k]) if k in kept else None for k in range(n)]
        self.emit({"event": "block_started", "block": loop, "attempt": 1})
        said = f"{n} item{'s' if n != 1 else ''}, {at_once} at the same time"
        if kept:
            numbers = [str(k + 1) for k in sorted(kept)]
            said += (f"; item {numbers[0]} keeps its result" if len(numbers) == 1 else
                     f"; items {', '.join(numbers[:-1])} and {numbers[-1]} keep their results")
            said += f" from run #{self.inputs_from}, where {'it' if len(numbers) == 1 else 'they'} succeeded"
        self.emit({"event": "log", "block": loop, "level": "INFO", "message": said})
        source = next((e for e in self.flow.edges if e.dst == loop and e.dst_port == RESULT), None)
        part = {b for b in self.taking_part if loop in containers(self.flow, b)}  # the blocks inside it that run
        todo = [k for k in range(n) if records[k] is None]
        running, ended, tally, failing = set(), {}, {}, set()
        changed = threading.Condition()

        def run_item(k: int):
            status, result, failed, missing = "failed", None, [], False
            try:
                given = {(loop, ITEM): items[k], (loop, INDEX): k + 1}
                runner = self._item_runner(loop, f"[item {k + 1}/{n}]", k + 1, given, part, tally, changed, failing)
                status = runner.run()
                if source is not None:
                    result = runner.outputs.get((source.src, source.src_port))
                    missing = (source.src, source.src_port) not in runner.outputs
                failed = [f"{b} failed: {runner.errors[b]}" if b in runner.errors else f"{b} failed"
                          for b in runner.unhandled()]
            except BaseException as e:  # a bug of Taskloom's: the run fails with it
                self.crashed.append(e)
            finally:
                with changed:
                    ended[k] = (status, result, failed, missing)
                    changed.notify_all()

        def progress(finished: int):
            self.emit({"event": "progress", "block": loop, "done": finished, "of": n, "running": len(running)})

        finished, stopping = len(kept), False
        progress(finished)
        while True:
            with changed:
                starting = []
                while (todo and len(running) < at_once and not stopping and not failing and not self.cancel.is_set()
                       and not self.crashed):
                    starting.append(todo.pop(0))
                    running.add(starting[-1])
                if not running:
                    break
                if not starting:
                    with self.watch.idle():  # (until an item ends)
                        while not running & set(ended):
                            changed.wait(0.05)
                ending = sorted(running & set(ended))
                for k in ending:
                    running.discard(k)
                    stopping |= ended[k][0] != "success"
                finished += sum(ended[k][0] != "cancelled" for k in ending)  # (stopped: not done)
            for k in starting:  # (in item order)
                threading.Thread(target=self.watch.at_work(run_item), args=(k,), daemon=True,
                                 name=f"{loop}-item-{k + 1}").start()
            if starting or ending:
                progress(finished)
        for k, (status, result, _, _) in ended.items():
            records[k] = (items[k], status, result)
        if not self.item:  # (inside an item of another Loop: that Loop tells it, for all its items)
            self._tell_items(tally)
        if self.cancel.is_set():
            self.emit({"event": "block_finished", "block": loop, "status": "cancelled", "attempts": 1})
            return "cancelled", {}, records
        failed = {k + 1: why for k, (status, _, why, _) in sorted(ended.items()) if status != "success"}
        if failed:
            error = f"{len(failed)} of {n} items failed: " + ", ".join(
                f"item {k} ({'; '.join(why) or 'it did not end'})" for k, why in failed.items())
            return (*self._fail(spec, 1, Failed(error + (f"; {len(todo)} not run" if todo else "")), inputs), records)
        missing = [str(k + 1) for k, (_, _, _, nothing) in sorted(ended.items()) if nothing]
        if missing:
            self.emit({"event": "log", "block": loop, "level": "WARNING", "message": (
                f"nothing reached {loop}.result for item{'s' if len(missing) > 1 else ''} {', '.join(missing)} (from "
                f"{source.src}.{source.src_port}): {'their results are' if len(missing) > 1 else 'its result is'} "
                "None")})
        produced = {RESULTS: [record[2] for record in records]}
        self.emit({"event": "log", "block": loop, "level": "INFO", "message": f"{n} item{'s' if n != 1 else ''} done"})
        self._succeeded(loop, produced, started)
        return "success", {**produced, DONE: True}, records

    def _succeeded(self, block_id: str, produced: dict, started: float):
        """A Loop's or a Merger's end, over its runs: success, with what it passes on."""
        self.emit({"event": "block_finished", "block": block_id, "status": "success", "attempts": 1,
                   "duration": round(time.monotonic() - started, 3), "summary": _summary(produced),
                   "tables": {where: path for p, v in produced.items() for where, path in _table_files(v, p).items()},
                   "previews": {p: preview(v) for p, v in produced.items()}})

    def _item_runner(self, block: str, tag: str, number: int, given: dict, part: set, tally: dict, changed,
                     failing: set | None = None) -> "_Runner":
        """A runner for the blocks `part`, once for its run number `number` (from 1): an item of the Loop `block` (those
        directly inside it, see run), or a line of the Merger `block` set to each line as it finishes (the blocks after
        it). They get `given` (the item and its index, or the line's output) and what the blocks before them hold (each
        block its own copy); the run's Log lines and events carry its tag (`tag`: [item 2/5], [line load_sales]); each
        block's end goes into `tally` (under `changed`), for _tell_items. A Loop's: number - 1 into `failing` once a
        block directly inside it fails with nothing connected to its error output (nothing can handle it: the item
        fails)."""
        loop = self.flow.blocks[block].type == LOOP

        def emit(event: dict):
            event = dict(event)
            if event["event"] == "log":
                event["message"] = f"{tag} {event['message']}"
            elif event.get("block"):
                event["item"] = f"{tag} {event['item']}" if event.get("item") else tag
            if event["event"] == "block_finished" and "items" not in event:
                with changed:
                    tally.setdefault(event["block"], []).append(dict(event))
                    if (loop and event["status"] == "failed" and self.flow.blocks[event["block"]].inside == block
                            and not any(e.src == event["block"] and e.src_port == ERROR_PORT for e in self.flow.edges)):
                        failing.add(number - 1)
            self.emit(event)

        runner = _Runner(self.flow, self.registry, self.home, self.values, self.now, self.funcs, emit, self.cancel,
                         self.workspace / block / ("items" if loop else "lines") / str(number), self.answers)
        runner.loop = block if loop else self.loop
        runner.item, runner.ordered, runner.sections, runner.watch = tag, self.ordered, self.sections, self.watch
        runner.run_id = self.run_id
        runner.taking_part, runner.outside, runner.blocked, runner.parent = part, self.outside, set(self.blocked), self
        runner.outputs = {**self.outputs, **given}
        return runner

    def _tell_items(self, tally: dict):
        """How each block inside a Loop ended over its items (e.g. ✓ 4 ✕ 1), or each block after a Merger set to
        each line as it finishes over its lines' runs, with what the last to end gave it: told as its result in the run
        (`tally`: each block's ends, in the order they came)."""
        marks = {"success": "✓", "failed": "✕", "skipped": "skipped", "cancelled": "cancelled"}
        for block, ends in tally.items():
            statuses, last = [e["status"] for e in ends], ends[-1]
            self.emit({**{k: last[k] for k in ("attempts", "summary", "error", "tables", "previews") if k in last},
                       "event": "block_finished", "block": block,
                       "status": next(s for s in ("failed", "cancelled", "success", "skipped") if s in statuses),
                       "items": {"ended": " ".join(f"{mark} {statuses.count(s)}" for s, mark in marks.items()
                                                   if s in statuses),
                                 "last": last["item"], "status": last["status"]}})

    def _earlier_items(self, loop: str, items: list) -> dict:
        """Running from the Loop (or only it): its items that succeeded in the run this one carries on from, the same
        items now, with the results they gave there ({index: result}); not one whose tables are gone."""
        found = {}
        for k, record in enumerate((_saved(self.workspace, loop).get(ITEM_RESULTS) or [])[:len(items)]):
            if record is not None and record[1] == "success" and _same(_map_marks(record[0], Table), items[k]):
                result = _map_marks(record[2], Table)
                if all(Path(path).exists() for path in _table_files(result, RESULT).values()):
                    found[k] = result
        return found

    def _run_per_line(self, spec, given: dict | None) -> tuple[str | None, dict, list | None]:
        """Run a Merger set to each line as it finishes: as each line connected to its lines delivers, the blocks after
        it (each_line) once for that line, as a flow of their own (_item_runner), getting the line's output itself; At
        the same time of those runs at once, the next starting as soon as one ends, in the order the lines finished.
        Its Log says as each line arrives, or that it has no run (it failed, or delivered nothing); a failed run stops
        no other. Once every line has been dealt with, it fails if a line or a run failed, listing them. What leaves
        the runs (into a Merger set to all lines together, which closes them) is {line name: what that run gave it}
        (PerLine), as its own output is. No line delivered: it ends as when it joins. Running from it (or only it),
        `given` holds its inputs: the lines' outputs in the run this one carries on from; the lines whose runs
        succeeded there keep what those gave and do not run again. Returns how it ended (None: skipped), its outputs,
        and each line's (output, status, what its run gave), kept with its outputs for that (None: it had no run)."""
        merger, cls = spec.id, self.registry.get(spec.type)
        edges = [e for e in self.flow.edges if e.dst == merger and e.dst_port == LINES]
        names, n, started = line_names([f"{e.src}.{e.src_port}" for e in edges]), len(edges), time.monotonic()
        part = self.each_line[merger]
        # As a Loop: what it (but its lines), or the blocks it runs, take from outside them failed: it is skipped.
        stopped = [e for e in self.flow.edges if e.src in self.blocked and e.src_port != ERROR_PORT and (
            e.dst == merger and e.dst_port != LINES or e.dst in part and e.src not in part | {merger})]
        if given is None and stopped:
            self.failures.update(f for e in stopped for f in self.before_unhandled.get(e.src, ()))
            self.blocked.add(merger)
            self._keep_outputs(merger, {}, status=BLOCKED)
            self.emit({"event": "block_finished", "block": merger, "status": "skipped",
                       "summary": "something before it failed"})
            return None, {}, None
        try:
            at_once = _filled(cls, block_config(cls, spec), self.values, {}, set(), set())[AT_ONCE]
            as_finished = block_config(cls, spec)[RUNS_AGAIN] == AS_FINISHED
        except Exception as e:
            return (*self._fail(spec, 0, e, given or {}), None)
        leaving = list(dict.fromkeys((e.src, e.src_port) for e in self.flow.edges
                                     if e.src in part and e.dst not in part and e.dst in self.flow.blocks))
        values = {}  # each line's output, once it has arrived
        if given is not None:  # (by block.output, as it arrives; or, typed, by the line's name)
            got = given.get(LINES)
            for k, e in enumerate(edges):
                key = next((key for key in (f"{e.src}.{e.src_port}", names[k]) if isinstance(got, dict) and key in got),
                           None)
                if key is not None:
                    values[k] = got[key]
        kept = self._earlier_items(merger, [values.get(k, NO_VALUE) for k in range(n)]) if given is not None else {}
        records: list = [(values[k], "success", kept[k]) if k in kept else None for k in range(n)]
        self.emit({"event": "block_started", "block": merger, "attempt": 1})
        said = (f"{n} line{'s' if n != 1 else ''}, each passed on as it finishes: the blocks after it run once per "
                f"line, {at_once} at the same time")
        if kept:
            said += (f"; kept from run #{self.inputs_from}, where they succeeded: the runs for "
                     + ", ".join(names[k] for k in sorted(kept)))
        self.emit({"event": "log", "block": merger, "level": "INFO", "message": said})
        waiting = [k for k in range(n) if k not in kept]  # the lines not dealt with yet
        todo, running, ended, tally = [], set(), {}, {}

        def run_line(k: int):
            status, gave, failed = "failed", {}, []
            try:
                runner = self._item_runner(merger, f"[line {names[k]}]", k + 1, {(merger, "value"): values[k]}, part,
                                           tally, self.changed)
                status = runner.run()
                gave = {source: runner.outputs[source] for source in leaving if source in runner.outputs}
                failed = [f"{b} failed for line {names[k]}" + (f": {runner.errors[b]}" if b in runner.errors else "")
                          for b in runner.unhandled()]
            except BaseException as e:  # a bug of Taskloom's: the run fails with it
                self.crashed.append(e)
            finally:
                with self.changed:
                    ended[k] = (status, gave, failed)
                    self.changed.notify_all()

        def line_place(k: int) -> tuple:  # (line order: where the line reaches it on the canvas, top to bottom)
            ui = self.flow.blocks[edges[k].src].ui
            return ui.get("y", 0), ui.get("x", 0)

        def ended_line(k: int) -> bool:  # (one taking no part in this run ended in the run it carries on from)
            return given is not None or edges[k].src in self.settled or edges[k].src not in self.taking_part

        finished = len(kept)
        self.emit({"event": "progress", "block": merger, "done": finished, "of": n, "running": 0})
        over = False
        while not over:
            said = []
            with self.changed:
                arrived = [] if self.cancel.is_set() or self.crashed else [k for k in waiting if ended_line(k)]
                arrived.sort(key=lambda k: (1, self.ended.index(edges[k].src), "") if edges[k].src in self.settled
                             else (0, self.earlier_ends.get(edges[k].src, "") if as_finished else "",
                                   line_place(k)))
                for k in arrived:
                    waiting.remove(k)
                    source = (edges[k].src, edges[k].src_port)
                    if given is None and source in self.outputs:
                        values[k] = self.outputs[source]
                    if k in values:
                        todo.append(k)
                        continue
                    finished += 1
                    failed = self._failed_lines(merger, [edges[k]])
                    said.append(f"{names[k]} has no run: " + ("; ".join(failed) if failed else "something before it "
                                "failed" if edges[k].src in self.blocked else "it delivered nothing"))
                starting = []
                while todo and len(running) < at_once and not self.cancel.is_set() and not self.crashed:
                    starting.append(todo.pop(0))
                    running.add(starting[-1])
                said += [f"{names[k]} arrived: its run {'starts' if k in starting else 'waits for its turn'}"
                         for k in arrived if k in values]
                ending = sorted(running & set(ended))
                running -= set(ending)
                finished += sum(ended[k][0] != "cancelled" for k in ending)  # (stopped: not done)
                over = not running and (not (waiting or todo) or self.cancel.is_set() or self.crashed)
                if not (over or arrived or starting or ending):
                    with self.watch.idle():  # (until a run ends, or a line; stopped, no new one is dealt with: until
                        # no run is left)
                        while not (running & set(ended) or (not running if self.cancel.is_set() or self.crashed
                                                            else any(ended_line(k) for k in waiting))):
                            self.changed.wait(0.05)
            for message in said:
                self.emit({"event": "log", "block": merger, "level": "INFO", "message": message})
            for k in starting:  # (in the order the lines finished)
                threading.Thread(target=self.watch.at_work(run_line), args=(k,), daemon=True,
                                 name=f"{merger}-line-{k + 1}").start()
            if arrived or starting or ending:
                self.emit({"event": "progress", "block": merger, "done": finished, "of": n, "running": len(running)})
        for k, (status, gave, _) in ended.items():
            records[k] = (values[k], status, gave)
        if not self.item:  # (in the run of an item or a line: that one tells it, for all its runs)
            self._tell_items(tally)
        if self.cancel.is_set():
            self.emit({"event": "block_finished", "block": merger, "status": "cancelled", "attempts": 1})
            return "cancelled", {}, records
        if not values:  # as when it joins: it fails naming the lines that failed, or is skipped saying why
            verdict = self._gather(merger) if given is None else Failed("lines: no line's output arrived")
            return (None, {}, None) if verdict is None else (*self._fail(spec, 1, verdict, given or {}), records)
        failed = self._failed_lines(merger, edges) + [
            why for k, (status, _, whys) in sorted(ended.items()) if status != "success"
            for why in (whys or [f"the run for line {names[k]} failed"])]
        if failed:
            return (*self._fail(spec, 1, Failed("; ".join(failed)), given or {}), records)
        ran = [k for k in range(n) if records[k] is not None]
        for source in leaving:  # what leaves the runs: by line
            missing = [names[k] for k in ran if source not in records[k][2]]
            if missing:
                self.emit({"event": "log", "block": merger, "level": "WARNING", "message": (
                    f"nothing left {source[0]}.{source[1]} in the run{'s' if len(missing) > 1 else ''} for "
                    f"{', '.join(missing)}: what comes after the runs gets nothing from it")})
            else:
                self.outputs[source] = PerLine((names[k], records[k][2][source]) for k in ran)
        produced = {"value": PerLine((names[k], values[k]) for k in sorted(values))}  # (the Merger's output)
        self.emit({"event": "log", "block": merger, "level": "INFO",
                   "message": f"{n} line{'s' if n != 1 else ''} done"})
        self._succeeded(merger, produced, started)
        return "success", produced, records

    def _run_one(self, block_id: str):
        if self.cancel.is_set():
            self.emit({"event": "block_finished", "block": block_id, "status": "cancelled"})
            return
        alone = block_id in (self.only, self.run_from)  # its inputs were worked out for this run
        each_line = block_id in self.each_line  # (its lines arrive as it runs)
        inputs = (Failed(f"the run cannot go on: it waits for {self.ending[block_id]}") if block_id in self.ending
                  else self.only_inputs if alone else {} if each_line else self._gather(block_id))
        if inputs is None:
            return
        self.ran.add(block_id)
        if alone:  # what it gets (maybe values you chose), saved before it can change them
            self._keep_outputs(block_id, inputs, KEPT_INPUTS, typed=self.only_typed, came=self.only_came)
            self.brought = {name: _by_edge(inputs[name], came) for name, came in self.only_came.items()}
        spec = self.flow.blocks[block_id]
        # A Loop's: each item's (item, status, result); a Merger's each line: each line's (output, status, what its run
        # gave). For running from it again.
        records = None
        if isinstance(inputs, Failed):  # (a Merger: a line failed; or what it waits for can never come)
            status, produced = self._fail(spec, 0, inputs, {})
            if each_line:  # (no line ran the blocks it runs once per line)
                self._skip_per_line(block_id, blocked=True)
        elif spec.type == LOOP:
            status, produced, records = self._run_loop(spec, inputs)
        elif each_line:
            status, produced, records = self._run_per_line(spec, inputs if alone else None)
            if status != "cancelled" and not any(records or ()):  # (it failed or was skipped: no line ran them)
                self._skip_per_line(block_id, blocked=status == "failed" or block_id in self.blocked)
            if status is None:  # (skipped, as when it joins)
                self.ran.discard(block_id)
                self.blocked |= self._at_level(self.each_line[block_id]) if block_id in self.blocked else set()
                return
        else:
            status, produced = self._run_block(spec, inputs)
        self._keep_outputs(block_id, produced if records is None else {**produced, ITEM_RESULTS: records},
                           status=status)
        if status == "failed":
            self.blocked.add(block_id)
            self.failures.add(block_id)
            self.blocked |= self._at_level(self.each_line.get(block_id, set()))  # (the blocks run once per line did not run)
        for port, value in produced.items():
            self.outputs[(block_id, port)] = value

    def _gather(self, block_id: str) -> dict | Failed | None:
        """The block's inputs from the blocks before it; None (and it is skipped) when it cannot run; for a Merger
        waiting for a line that failed, the failure it fails with."""
        # A run-after connection from a block taking no part in the run does not hold it back. (A Loop's result is
        # for each item, from the blocks inside it.)
        incoming = [e for e in self.flow.edges if e.dst == block_id and not from_inside(self.flow, e)
                    and not (e.dst_port == AFTER and e.src in self.outside)]
        merger = self.flow.blocks[block_id].type == MERGER
        if merger and (failed := self._failed_lines(block_id, incoming)):
            return Failed("; ".join(failed))
        # What the blocks inside a Loop take from outside it holds the Loop back too.
        around = [e for e in self.flow.edges if self.flow.blocks[block_id].type == LOOP and e.src in self.flow.blocks
                  and block_id in containers(self.flow, e.dst)
                  and block_id not in [e.src, *containers(self.flow, e.src)]]
        # Nothing downstream of a failure runs: an edge from a normal output of a failed block (or of
        # a block skipped because of a failure) stops this block. Error outputs are the way out.
        if any(e.src in self.blocked and e.src_port != ERROR_PORT for e in incoming + around):
            self.failures.update(f for e in incoming + around if e.src_port != ERROR_PORT
                                 for f in self.before_unhandled.get(e.src, ()))
            self.blocked.add(block_id)
            self._keep_outputs(block_id, {}, status=BLOCKED)
            self.emit({"event": "block_finished", "block": block_id, "status": "skipped",
                       "summary": "something before it failed"})
            return None
        in_ports = self._input_ports(self.flow.blocks[block_id])
        inputs, ready, after_failure, empty = {}, True, False, {}
        for name in dict.fromkeys(e.dst_port for e in incoming):
            port = in_ports.get(name)
            edges = [e for e in incoming if e.dst_port == name]
            delivered = [(e, self.outputs[(e.src, e.src_port)]) for e in edges if (e.src, e.src_port) in self.outputs]
            missing = [e for e in edges if (e.src, e.src_port) not in self.outputs]
            # Usually every connected edge must deliver; an any_of input needs just one (paths that
            # meet after an If, or the error outputs of several blocks going to one handler).
            if not delivered or (missing and not getattr(port, "any_of", False)):
                ready = False
                after_failure |= any(e.src in self.blocked for e in missing)
                self.failures.update(f for e in missing for f in self.before_unhandled.get(e.src, ()))
                empty[name] = missing
                continue
            inputs[name] = _arriving(port, edges, delivered)
        by_line = merger and any(isinstance(v, PerLine) for v in inputs.get(LINES, {}).values())
        if by_line:  # (it closes the runs of a Merger set to each line as it finishes)
            inputs[LINES] = _by_line(inputs[LINES])
        if not ready:
            if after_failure:  # it did not get its input because something before it failed
                self.blocked.add(block_id)
            self._keep_outputs(block_id, {}, status=BLOCKED if after_failure else SKIPPED)
            first = next(iter(empty))
            lines = [e for e in incoming if e.dst_port == LINES]  # (a Merger's: named as it names them)
            why = "something before it failed" if after_failure else (
                "nothing reached its run-after" if first == AFTER else
                "nothing arrived from " + ", ".join(e.src if [x.src for x in lines].count(e.src) == 1 else
                                                    f"{e.src}.{e.src_port}" for e in empty[first])
                if merger and first == LINES else f"nothing reached its '{first}' input")
            self.emit({"event": "block_finished", "block": block_id, "status": "skipped", "summary": why})
            return None
        if by_line:  # what running it again on its own gets (the runs' blocks keep nothing at this level)
            self._keep_outputs(block_id, inputs, KEPT_INPUTS)
        return inputs

    def _failed_lines(self, merger: str, incoming: list) -> list[str]:
        """For a Merger: the failures, which no error output handles, of the lines it waits for that delivered nothing
        because of them ("load_costs failed: ..." for each). A failure before the lines it joins is none of theirs."""
        stopped = [e.src for e in incoming if e.dst_port == LINES and e.src in self.blocked
                   and (e.src, e.src_port) not in self.outputs]
        joined = [its for its in self.lines.values()  # the Splitters whose lines it joins
                  if any(e.src in blocks for e in incoming for _, blocks in its)]
        in_lines = {b for its in joined for _, blocks in its for b in blocks}
        failed = dict.fromkeys(f for src in stopped for f in self._failed_behind(src) if f in in_lines)
        return [f"{f} failed: {self.errors[f]}" if f in self.errors else f"{f} failed in the run this one carries on from"
                for f in failed if not any(e.src == f and e.src_port == ERROR_PORT and e.dst in self.taking_part
                                           for e in self.flow.edges)]

    def _skip_per_line(self, merger: str, blocked: bool):
        """The blocks a Merger set to each line as it finishes runs once per line, when no line ran them: skipped, as
        after a Merger that joins (because something before them failed, or nothing arrived)."""
        why = "something before it failed" if blocked else f"nothing arrived from {merger}"
        for b in [b for b in self.ordered if b in self._at_level(self.each_line[merger])]:
            self._keep_outputs(b, {}, status=BLOCKED if blocked else SKIPPED)
            self.emit({"event": "block_finished", "block": b, "status": "skipped", "summary": why})

    def _at_level(self, blocks: set[str]) -> set[str]:
        """Those of `blocks` this runner runs itself (not the blocks inside a Loop among them: the Loop runs those)."""
        return {b for b in blocks if self.flow.blocks[b].inside == self.loop}

    def _failed_behind(self, block: str) -> list[str]:
        """The failed blocks that stopped `block`: it, if it failed; else those before it (it was skipped because
        something before it failed)."""
        if block in self.errors:
            return [block]
        if block in self.before_unhandled:  # (before the blocks of a re-run: as the run it carries on from had it)
            return sorted(self.before_unhandled[block])
        return [f for e in self.flow.edges if e.dst == block and e.src in self.blocked and e.src_port != ERROR_PORT
                for f in self._failed_behind(e.src)]

    def _keep_outputs(self, block_id: str, produced: dict, file: str | None = None, status: str | None = None,
                      typed: dict | None = None, came: dict | None = None):
        """Save what the block produced and how it ended (or, with KEPT_INPUTS, what it got, which of
        those you typed and the connections the others came by), so it can later feed a re-run of a single
        block. Values holding a password are never written to disk."""
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
        if came:
            kept[CAME] = came
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
            if self.copies:  # its own copy of what it gets (see run)
                try:
                    inputs = copy.deepcopy(inputs)
                except Exception as e:
                    raise Failed(f"what it got cannot be copied ({type(e).__name__}: {e}); inside a Loop, and in a run "
                                 "with a Splitter's lines, each block gets its own copy of what it receives") from None
            config = block_config(cls, spec)
            in_ports, out_ports = cls.ports(config)
            connected = {e.dst_port for e in self.flow.edges if e.dst == spec.id}
            config = _filled(cls, config, self.values, inputs, set(in_ports) - {AFTER}, connected)
            for name, port in in_ports.items():
                if port.kind == "table" and name in inputs and not isinstance(inputs[name], Table):
                    raise TypeError(f"input '{name}' expects a table, got {type(inputs[name]).__name__}")
        except Exception as e:
            return self._fail(spec, 0, e, inputs)

        jar = self._session_cookies(spec) if spec.session and spec.type == API_REQUEST else None
        retry = spec.retry
        for attempt in range(1, retry.max_attempts + 1):
            self.emit({"event": "block_started", "block": spec.id, "attempt": attempt})
            attempt_cancel = threading.Event()
            ctx = Context(
                block_id=spec.id, attempt=attempt, params=self.values, as_of=self.now, functions=self.funcs,
                workspace=self.workspace / spec.id / f"attempt-{attempt}", home=self.home,
                emit=self.emit, run_cancel=self.cancel, attempt_cancel=attempt_cancel, answers=self.answers,
                registry=self.registry, cookies=jar, run_id=self.run_id, flow_path=str(self.flow.path),
            )
            started = time.monotonic()
            try:
                own = inputs if AFTER in in_ports else {k: v for k, v in inputs.items() if k != AFTER}
                try:
                    result = self._attempt(cls(config), ctx, own, spec.timeout, attempt_cancel)
                finally:
                    if jar is not None:  # (after each request: the cookies' names and domains, never their values)
                        self.emit({"event": "log", "block": spec.id, "level": "INFO",
                                   "message": f"session {spec.session}'s cookies: {_cookie_names(_held(jar))}"})
                produced = self._check_outputs(result, out_ports, ctx)
                # A Splitter given no value passes None on to its lines: not a value to show as if it were one.
                summary = ("its lines start with nothing: no value arrived" if spec.type == SPLITTER
                           and "value" not in own else _summary(produced))
            except Exception as e:
                # Stopped: whatever the block raised as it stopped (e.g. a closed query or connection) is not a failure.
                if isinstance(e, Cancelled) or self.cancel.is_set():
                    self.emit({"event": "block_finished", "block": spec.id, "status": "cancelled", "attempts": attempt})
                    return "cancelled", {}
                if attempt == retry.max_attempts:
                    return self._fail(spec, attempt, e, inputs)
                delay = retry.delay_after(attempt)
                self.emit({"event": "log", "block": spec.id, "level": "ERROR",
                           "message": f"attempt {attempt}/{retry.max_attempts} failed: {_told(e)}"})
                self.emit({"event": "log", "block": spec.id, "level": "RETRY",
                           "message": f"waiting {delay:.3g} s before attempt {attempt + 1}/{retry.max_attempts}"})
                if self.cancel.wait(delay):
                    self.emit({"event": "block_finished", "block": spec.id, "status": "cancelled", "attempts": attempt})
                    return "cancelled", {}
                continue
            self.emit({"event": "block_finished", "block": spec.id, "status": "success", "attempts": attempt,
                       "duration": round(time.monotonic() - started, 3), "summary": summary,
                       "tables": {where: path for p, v in produced.items()
                                  for where, path in _table_files(v, p).items()},
                       "previews": {p: preview(v) for p, v in produced.items()}})
            if has_done_output(cls, out_ports):
                produced[DONE] = True  # for blocks connected done -> after
            return "success", produced
        raise AssertionError("unreachable")

    def _session_cookies(self, spec) -> http.cookiejar.CookieJar:
        """The cookie jar of the Session the API Request `spec` is inside: one for the run (for each item of a Loop the
        Session is inside), which the lines running at the same time share (it has a lock of its own). What it holds
        just before the block runs is kept with the block's outputs (KEPT_COOKIES), for running again from it: a run
        that carries on from another starts it with the cookies the Session held just before the first of its blocks
        to send a request did there, and the Log says so."""
        level, owner = self.flow.sessions.get(spec.session), self
        while owner.parent is not None and (owner.loop != level or owner.parent.loop == owner.loop):
            owner = owner.parent  # (a line's: the runner's whose line it is)
        with _SESSIONS:
            jar = owner.jars.get(spec.session)
            if jar is None:
                jar = owner.jars[spec.session] = http.cookiejar.CookieJar()
                if owner.inputs_from is not None:
                    kept, run = owner.workspace / spec.id / KEPT_COOKIES, f"run #{owner.inputs_from}"  # (carried over)
                    if kept.exists():
                        for cookie in pickle.loads(kept.read_bytes()):
                            jar.set_cookie(cookie)
                        said = (f"session {spec.session}'s cookies come from {run}, as they were just before {spec.id} "
                                f"there: {_cookie_names(_held(jar))}")
                    else:
                        said = f"session {spec.session} starts with no cookies: {run} kept none for {spec.id}"
                    self.emit({"event": "log", "block": spec.id, "level": "INFO", "message": said})
            held = _held(jar)
        folder = self.workspace / spec.id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / KEPT_COOKIES).write_bytes(pickle.dumps(held))
        return jar

    def _fail(self, spec, attempts, error, inputs) -> tuple[str, dict]:
        block_id = spec.id
        cls = self.registry.get(spec.type)
        # Scrubbed here too: the error goes on to error handlers, which may email it.
        # Blocks can attach a clearer report (Python Code: the code around the line, its variables); a failure the
        # block expects (Failed) has nothing to add to its message.
        message = redact(_told(error))
        tb = "" if isinstance(error, Failed) else redact(
            getattr(error, "details", None) or "".join(traceback.format_exception(error)))
        report = [f"{block_id} ({cls.title if cls else spec.type}) failed: {message}"]
        incoming = [e for e in self.flow.edges if e.dst == block_id and not from_inside(self.flow, e)]
        if incoming:
            report.append("Inputs:")
            for e in incoming:
                if block_id in (self.only, self.run_from):  # its inputs were worked out for this run (only_inputs)
                    value = (self.brought[e.dst_port].get((e.src, e.src_port), NO_VALUE) if e.dst_port in self.brought
                             else inputs.get(e.dst_port, NO_VALUE))  # typed (or left empty): by no connection
                else:
                    value = self.outputs.get((e.src, e.src_port), NO_VALUE)
                value = "(nothing arrived)" if value is NO_VALUE else inline(value)
                report.append(f"  {e.dst_port} ← {e.src}.{e.src_port}: {value}")
        if tb:
            report.append(tb)
        self.errors[block_id] = message
        self.emit({"event": "log", "block": block_id, "level": "ERROR", "message": redact("\n".join(report))})
        self.emit({"event": "block_finished", "block": block_id, "status": "failed", "attempts": attempts, "error": message})
        return "failed", {ERROR_PORT: {"block": block_id, "error": message, "traceback": tb, "attempts": attempts,
                                       **redact(getattr(error, "carried", {}))}}

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
                # Finished in the grace without having seen the stop: its work is done, so keep it (a retry would
                # redo it, e.g. send an email twice). One that saw the stop may have returned part of its work.
                if done.wait(STOP_GRACE_SECONDS) and "error" not in result and not ctx._saw_stop:
                    return result["value"]
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
            value = _as_tables(value, ctx, port)
            if out_ports[port].kind == "table" and not isinstance(value, Table):
                raise TypeError(f"output '{port}' must be a table, got {type(value).__name__}")
            produced[port] = value
        return produced


KEPT_OUTPUTS = "outputs.pickle"
ITEM_RESULTS = "__items__"  # with a Loop's outputs: each item's (item, status, result), None for one not run
KEPT_INPUTS = "inputs.pickle"  # what a block run on its own got: its inputs then, not what came before it
KEPT_COOKIES = "cookies.pickle"  # an API Request inside a Session: the Session's cookies just before it ran
STATUS = "__status__"  # how the block ended: success | failed | BLOCKED
BLOCKED = "blocked"  # skipped because something before it failed
SKIPPED = "skipped"  # skipped for another reason (e.g. a path not taken)
TYPED = "__typed__"  # inputs you typed, as you typed them
CAME = "__came__"  # the connections the other inputs came by (see _came)
STALE = "__stale__"  # made from older data: a block before it ran again on its own since
WITHHELD = "__withheld__"  # outputs that were produced but not saved (passwords, things that cannot be saved)


# What a placeholder can write into a setting: a single value (a bool is an int; a datetime is a date).
SINGLE_VALUES = (str, int, float, decimal.Decimal, dt.date, dt.time)


def _filled(cls, config: dict, params: dict, inputs: dict, data_inputs: set, connected: set) -> dict:
    """The block's config with the placeholders of its templated settings filled in (validate checked their names): a
    flow parameter, or what arrived at the block's data input of that name (not in SQL code); a number setting written
    with placeholders is then read as its number. Raises Failed, naming the setting, for one it cannot fill."""
    def fill(label: str, text: str, own: set) -> str:
        values = dict(params)
        for name in sorted(P.template_names(text) & own):
            if name not in inputs:
                why = "nothing arrived at" if name in connected else "nothing is connected to"
                raise Failed(f"{label}: {{{name}}}: {why} the input {name}")
            value = values[name] = inputs[name]
            if not isinstance(value, SINGLE_VALUES):
                got = ("a table" if isinstance(value, Table) or Table.is_frame(value) else
                       "None" if value is None else f"a {type(value).__name__}")
                raise Failed(f"{label}: {{{name}}}: the input {name} got {got}, but only a single value (text, a "
                             "number, a date or time, yes/no) can be written into a setting")
        try:
            return P.render(text, values)
        except ValueError as e:
            raise Failed(f"{label}: {e}") from None

    filled = dict(config)
    for key, f in cls.config.items():
        if not f.template:
            continue
        value, label = config[key], setting_label(key, f)
        sql = isinstance(f, fields.Code) and f.language == "sql"
        own = set() if sql else data_inputs  # SQL code: flow parameters only
        if isinstance(value, list):  # (a table's rows, fields.KeyValues: in each key and value)
            filled[key] = [{k: fill(label, v, own) if k in ("key", "value") else v for k, v in item.items()}
                           if isinstance(item, dict) else fill(label, item, own) for item in value]
        elif isinstance(value, str):
            filled[key] = fill(label, value, own)
            if isinstance(f, (fields.Int, fields.Float)):  # (one written without placeholders is a number already)
                filled[key] = _number(f, label, value, filled[key])
    return filled


def _number(f, label: str, written: str, text: str):
    """The number a number setting written as `written` takes, filled in as `text`, within its own limits."""
    whole = isinstance(f, fields.Int)
    try:
        number = int(text) if whole else float(text)  # (spaces around it ignored)
    except ValueError:
        raise Failed(f"{label}: {written} gave {text!r}, which is not a {'whole ' if whole else ''}number") from None
    try:
        return f.coerce(number)
    except ValueError as e:  # e.g. an Int's minimum
        raise Failed(f"{label}: {written} gave {text!r}: {e}") from None


def _items_of(value) -> list:
    """A Loop's items: a list's elements, or a table's rows, each a dict of its columns. Anything else fails it (a text
    is not run letter by letter)."""
    if isinstance(value, Table):
        return value.scan().collect().to_dicts()
    if isinstance(value, (list, tuple)):
        return list(value)
    got = "None" if value is None else f"a {'text' if isinstance(value, str) else type(value).__name__}"
    raise Failed(f"items: a Loop runs once per item of a list (or per row of a table), but got {got}: "
                 f"{inline(value, 80)}")


def _same(a, b) -> bool:
    """Whether two items are the same (two that cannot be compared are not)."""
    try:
        return bool(a == b)
    except Exception:
        return False


def _holds_secret(value) -> bool:
    if isinstance(value, SecretStr):
        return not isinstance(value, HiddenStr)
    if isinstance(value, dict):
        return any(_holds_secret(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_holds_secret(v) for v in value)
    return False


NO_VALUE = object()  # an input you left empty: the block gets no value for it


def _held(jar: http.cookiejar.CookieJar) -> list:
    """The cookies in a Session's jar, read while no request of a line running meanwhile changes them."""
    with jar._cookies_lock:  # (the lock http.cookiejar takes itself as requests send cookies and responses set them)
        return list(jar)


def _cookie_names(cookies: list) -> str:
    """Cookies as the Log tells them: their names and domains, never their values."""
    return ", ".join(f"{c.name} ({c.domain})" for c in cookies) or "none"


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
    return _block_inputs(flow, registry, home, only, run_id)[:3]


def _block_inputs(flow: Flow, registry, home, only: str, run_id: int) -> tuple[dict, dict, dict, dict]:
    """block_inputs, and the connections the inputs not typed came by ({input: see _came})."""
    input_ports(flow, registry, only)  # a block that exists
    cleaned = f"the files of run #{run_id} were cleaned up (Keep runs, keep_runs_for)"
    run_dir = home.runs_dir / str(run_id)
    if not run_dir.is_dir():
        raise ValueError(cleaned)
    connected = list(dict.fromkeys(e.dst_port for e in flow.edges if e.dst == only and not from_inside(flow, e)))

    def table(path: Path) -> Table:
        if not path.exists():
            raise ValueError(cleaned)
        return Table(path)

    ran = _saved(run_dir, only).get(STATUS) in ("success", "failed", "cancelled")  # it got its inputs
    if ran and (run_dir / only / KEPT_INPUTS).exists():
        kept = pickle.loads((run_dir / only / KEPT_INPUTS).read_bytes())
        missing = {port: f"{only}.{port} held a password (or something that cannot be saved), so it was not kept"
                   for port in kept.pop(WITHHELD, [])}
        typed, came = kept.pop(TYPED, {}), kept.pop(CAME, {})
        return {port: _map_marks(value, table) for port, value in kept.items()}, missing, typed, came
    verdict, ports = _Now(flow, registry, run_dir, run_id).inputs(only)
    if verdict in ("blocked", "unknown"):
        return {}, {port: ports for port in connected}, {}, {}
    values, missing, came = {}, {}, {}
    for port, got in ports.items():
        if isinstance(got, str):
            missing[port] = got
            continue
        delivered = []
        for edge, saved in got:
            withheld = edge.src_port in saved.get(WITHHELD, [])
            if withheld and port != AFTER:  # into run after it arrived all the same: its value is not used
                missing[port] = (f"{edge.src}.{edge.src_port} held a password (or something that cannot be saved), "
                                 "so it was not kept")
                break
            delivered.append((edge, None if withheld else _map_marks(saved[edge.src_port], table)))
        else:  # as the runner hands them over
            edges = [e for e in flow.edges if e.dst == only and e.dst_port == port]
            in_port = input_ports(flow, registry, only).get(port)
            values[port], came[port] = _arriving(in_port, edges, delivered), _came(in_port, edges, delivered)
    return values, missing, {}, came


def _arriving(port, edges: list, delivered: list) -> object:
    """What an input gets from the edges into it (`delivered`: [(edge, value)] of those that delivered, in edge
    order): one edge gives the value; several (or into a `many` port) a list in edge order, or, into a by_source
    port (Python Code), {source block: value} in edge order. A block connecting several of its outputs there
    is keyed block.port (e.g. check.result, check.details), so none is lost; how the blocks are connected sets
    the keys, not which edges delivered. A port that is by_source and many (HTML Template's data) always gets
    {block.port: value}, even from one edge."""
    if getattr(port, "by_source", False) and getattr(port, "many", False):
        return {f"{e.src}.{e.src_port}": value for e, value in delivered}
    if getattr(port, "by_source", False) and len(edges) > 1:
        sources = [src for src, _ in {(e.src, e.src_port) for e in edges}]  # each output connected there, once
        return {(f"{e.src}.{e.src_port}" if sources.count(e.src) > 1 else e.src): value for e, value in delivered}
    values = [value for _, value in delivered]
    return values if getattr(port, "many", False) or len(edges) > 1 else values[0]


class PerLine(dict):
    """What leaves the runs of a Merger set to each line as it finishes, by an output (the Merger's own, or a block's
    run once per line): {line name: what that line's run gave}, for the Merger set to all lines together that closes
    the runs (_by_line)."""


def _by_line(lines: dict) -> dict:
    """A Merger's lines input ({block.output: value}) with what comes from the runs of a Merger set to each line as it
    finishes (PerLine) as one line per run, by its line's name: {line name: what that run gave}; from several of their
    outputs, {line name: {block.output: what that run gave there}}."""
    runs = {source: value for source, value in lines.items() if isinstance(value, PerLine)}
    joined = {source: value for source, value in lines.items() if source not in runs}
    if len(runs) == 1:
        return {**joined, **next(iter(runs.values()))}
    for source, per in runs.items():
        for line, value in per.items():
            joined.setdefault(line, {})[source] = value
    return joined


def line_names(sources: list[str]) -> list[str]:
    """The names a Merger gives the lines connected into its lines input (`sources`, each "block.output"), as Python
    Code names inputs from several blocks: the block's name, or block.output when a block connects several outputs."""
    blocks = [source.split(".")[0] for source in sources]
    return [source if blocks.count(block) > 1 else block for source, block in zip(sources, blocks)]


def _came(port, edges: list, delivered: list) -> object:
    """The connections an input's value came by (as _arriving takes them): that value, with each connection's
    part of it in place of its (block, output). Kept with the value, it tells which connection brought what
    (_by_edge), as the connections were then."""
    return _arriving(port, edges, [(e, (e.src, e.src_port)) for e, _ in delivered])


def _by_edge(value, came) -> dict:
    """What each connection brought ({(block, output): value}) out of an input's value and the connections it came
    by (_came)."""
    if isinstance(came, dict):
        return {source: value[key] for key, source in came.items()}
    if isinstance(came, list):
        return dict(zip(came, value))
    return {came: value}


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
        incoming = [e for e in self.flow.edges if e.dst == block and not from_inside(self.flow, e)
                    and not (e.dst_port == AFTER and self.state(e.src)[0] == "absent")]
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
    """What running from `block` on runs: it, and the blocks after it (with the blocks inside a Loop among them) that
    are on (a block connected to no start block is off)."""
    on = _taking_part(flow, registry, _start_blocks(flow, registry))
    after = _after(flow, block)
    after |= {b for b in flow.blocks if any(loop in after or loop == block for loop in containers(flow, b))}
    return {block} | {b for b in after if on is None or b in on}


def _from_before(flow: Flow, registry, home, part: set, run_id: int | None, start: str) -> tuple[dict, set, dict, set]:
    """For running the blocks in `part` (block `start` and the blocks after or inside it): what the blocks before
    them, which do not run, hold in run `run_id` ({(block, output): value}); which of them failed (or did
    not run because something before them failed); for each of those, the failures behind it whose error
    reached no block that ran ({block: failures}); and the ones that took no part in that run. Refuses
    (ValueError) when what one holds is not current. (`start` has its inputs worked out already.)"""
    before = [e for e in flow.edges if e.dst in part and e.src not in part and e.dst != start]
    if run_id is None:
        if before:
            raise ValueError(f"{before[0].dst} needs what {before[0].src} holds: say which run's to use (--inputs-from)")
        return {}, set(), {}, set()
    cleaned = f"the files of run #{run_id} were cleaned up (Keep runs, keep_runs_for)"
    run_dir = home.runs_dir / str(run_id)
    if not run_dir.is_dir():
        raise ValueError(f"{cleaned}: run the whole flow instead")

    def table(path: Path) -> Table:
        if not path.exists():
            raise ValueError(f"{cleaned}: run the whole flow instead")
        return Table(path)

    now, outputs, blocked, unhandled, absent = _Now(flow, registry, run_dir, run_id), {}, set(), {}, set()
    for edge in before:
        kind, saved, why = now.state(edge.src)
        if kind == "absent":
            absent.add(edge.src)
        if kind == "unknown":
            raise ValueError(f"{why}, or run from it")
        if kind in ("failed", BLOCKED):
            blocked.add(edge.src)  # as in a full run: nothing after it runs (but its error handlers)
            # A run it stops answers for these: it has failed unless an error handler of them runs in it.
            unhandled[edge.src] = _unhandled_failures(flow, now, edge.src)
        if kind in ("ran", "failed"):
            if edge.src_port in saved.get(WITHHELD, []):
                if edge.dst_port != AFTER:
                    raise ValueError(f"{edge.src}.{edge.src_port} held a password (or something that cannot be "
                                     "saved), so it was not kept: run the whole flow instead")
                outputs[(edge.src, edge.src_port)] = None  # it arrived all the same: run after does not use its value
            if edge.src_port in saved:
                outputs[(edge.src, edge.src_port)] = _map_marks(saved[edge.src_port], table)
    return outputs, blocked, unhandled, absent


def _unhandled_failures(flow: Flow, now: "_Now", block: str) -> set[str]:
    """`block` failed, or did not run because blocks before it failed: the failed blocks among those whose
    error reached no block that ran (an error handler that was skipped, or is off, handled nothing)."""
    def handled(failed: str) -> bool:
        return any(e.src == failed and e.src_port == ERROR_PORT and now.state(e.dst)[0] in ("ran", "failed")
                   for e in flow.edges)

    if now.state(block)[0] == "failed":
        return set() if handled(block) else {block}
    found, seen, todo = set(), {block}, [block]
    while todo:  # back through what stopped it: the error output of a failed block is the way out, but
        current = todo.pop()  # a block that did not run gave no error either (as in the runner's rules)
        for edge in flow.edges:
            if (edge.dst == current and edge.src not in seen
                    and (edge.src_port != ERROR_PORT or now.state(edge.src)[0] == BLOCKED)):
                seen.add(edge.src)
                kind = now.state(edge.src)[0]
                if kind == "failed" and not handled(edge.src):
                    found.add(edge.src)
                if kind == BLOCKED:
                    todo.append(edge.src)
    return found


def _only_inputs(flow: Flow, registry, home, only: str, run_id: int | None, given: dict,
                 texts: dict) -> tuple[dict, dict, dict]:
    """The inputs for running just `only`: the ones you give, the rest as they reached it in run `run_id`;
    the ones you typed (now, or for the inputs it keeps from that run), as you typed them; and the connections
    the others came by ({input: see _came})."""
    in_ports = input_ports(flow, registry, only)
    unknown = sorted(set(given) - set(in_ports))
    if unknown:
        raise ValueError(f"{only} has no input named '{unknown[0]}'")
    try:
        values, missing, typed, came = (_block_inputs(flow, registry, home, only, run_id) if run_id is not None
                                        else ({}, {}, {}, {}))
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
    return values, typed, {k: c for k, c in came.items() if k in values and k not in given}


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
            if edge.src == todo[-1] and edge.dst not in found and not from_inside(flow, edge):
                found.add(edge.dst)
                todo.insert(0, edge.dst)
        todo.pop()
    return found


def _as_tables(value, ctx, name: str):
    """`value` with each DataFrame in it (also inside dicts, lists and tuples, at any depth) stored as a Table, each
    in a file of its own (output.parquet; inside it by position, e.g. output.4.0.parquet). Other kinds of dicts and
    lists (e.g. a Counter) stay as they are."""
    if Table.is_frame(value):
        return Table.from_frame(value, ctx.new_path(f"{name}.parquet"))
    if type(value) is dict:
        return {k: _as_tables(v, ctx, f"{name}.{i}") for i, (k, v) in enumerate(value.items())}
    if type(value) in (list, tuple):
        return type(value)(_as_tables(v, ctx, f"{name}.{i}") for i, v in enumerate(value))
    return value


def _table_files(value, name: str) -> dict:
    """The file of each table in `value` (also inside lists and dicts) by where it is, e.g. output.by_region.north
    or output.parts[0]."""
    if isinstance(value, Table):
        return {name: str(value.path)}
    if isinstance(value, dict):
        parts = [(f"{name}.{k}", v) for k, v in value.items()]
    elif isinstance(value, (list, tuple)):
        parts = [(f"{name}[{i}]", v) for i, v in enumerate(value)]
    else:
        return {}
    return {where: path for part, v in parts for where, path in _table_files(v, part).items()}


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
    """Copy the saved outputs (and inputs, and a Session's cookies) of every block of an earlier run into a new run's
    folder. Table files are hard-linked (no extra space; they outlive the old run's folder), or copied.
    Saved inputs of blocks after the one that runs are left behind: its new outputs replace them."""
    kept_files = [*old.glob(f"*/{KEPT_OUTPUTS}"), *old.glob(f"*/{KEPT_COOKIES}"),
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


def run_part(flow: Flow, registry, trigger: str | None = None, only: str | None = None,
             run_from: str | None = None) -> set[str]:
    """The blocks a run takes part in (its arguments as run_flow's): just `only`; `run_from` and the blocks after it;
    or the lines of the start blocks that start it."""
    if only:  # (a Loop runs with the blocks inside it, a Merger set to each line as it finishes with those it runs)
        if only in flow.blocks and flow.blocks[only].type == LOOP:
            return {only} | {b for b in blocks_from(flow, registry, only) if only in containers(flow, b)}
        if (runs := per_line(flow, registry).get(only)) is not None:
            return {only} | (blocks_from(flow, registry, only) & runs)
        return {only}
    if run_from:
        return blocks_from(flow, registry, run_from)
    return _line(flow, registry, _fired_triggers(flow, registry, trigger))


def _line(flow: Flow, registry, fired: set[str]) -> set[str]:
    """The blocks a run started by the start blocks `fired` takes part in (their lines; the other start
    blocks take no part). Every block, in a flow without start blocks."""
    part = _taking_part(flow, registry, fired)
    return set(flow.blocks) if part is None else part - (_start_blocks(flow, registry) - fired)


def lines(flow: Flow, registry) -> dict[str, set[str]]:
    """Each start block's line, in the flow's order: the blocks the runs it starts take part in."""
    starts = _start_blocks(flow, registry)
    return {b: _line(flow, registry, {b}) for b in flow.blocks if b in starts}


def _fired_triggers(flow: Flow, registry, trigger: str | None) -> set[str]:
    """Start blocks that fire: the Schedule block of a scheduled run; Manual Start blocks of a manual
    run, or every start block when the flow has no Manual Start (so pressing Run always runs it)."""
    starts = _start_blocks(flow, registry)
    if trigger is not None:
        if trigger not in starts:  # e.g. renamed since the scheduler read the flow: nothing would run
            raise ValueError(f"there is no start block named '{trigger}'")
        return {trigger}
    manual = {b for b in starts if flow.blocks[b].type == "trigger.manual"}
    return manual or starts


def _apply_retention(home, history: History):
    """Delete the runs that every start block they count for lets go, as its Keep runs says (each flow file's
    start blocks apart), and the runs that count for none and started longer ago than keep_runs_for: their
    folders, then their history.

    A start block keeps its runs as its flow's newest run had it: a change applies to earlier runs too. Runs still
    in progress (possibly in other processes) are never touched, nor counted in the last N. A run whose folder
    cannot be deleted yet (e.g. a file in it is open on Windows) stays in the history, to be deleted as a later
    run ends.
    """
    settings_since = runs_kept_since(home.settings()).isoformat()
    rules = {start: _rule(*keep, settings_since) for start, keep in history.keeps().items()}
    # Only these can go (there can be millions of runs): the runs that started before the latest time a start
    # block (or Settings) lets runs go, and the runs of a flow with a start block that keeps the last N.
    before = max([settings_since, *(since for _, _, since in rules.values() if since)])
    counted = sorted({flow_path for (flow_path, _), (_, last, _) in rules.items() if last is not None})
    newer, gone = {}, []  # (flow file, start block) -> how many of its finished runs are newer than the one at hand
    for run_id, flow_path, status, started, counts_for in history.finished_runs(before, counted):
        starts = [(flow_path, block) for block in counts_for]
        if (all(_lets_go(rules[s], newer.get(s, 0), status, started) for s in starts) if starts
                else started < settings_since):
            gone.append(run_id)
        for s in starts:
            newer[s] = newer.get(s, 0) + 1
    for run_id in gone:
        folder = home.runs_dir / str(run_id)
        shutil.rmtree(folder, ignore_errors=True)
        if not folder.exists():
            history.forget(run_id)


def _rule(keep: str | None, keep_failed: bool, settings_since: str) -> tuple[bool, int | None, str | None]:
    """A start block's Keep runs and Always keep failed runs as (failed runs are kept, the last N runs are kept,
    the runs that started before this go); None: not by number, or not by time."""
    if keep is None:  # as in Settings
        return keep_failed, None, settings_since
    kind, number = kept_runs(keep)
    return keep_failed, number if kind == "last" else None, None if kind in ("all", "last") else (
        before_now(number, kind).isoformat())


def _lets_go(rule: tuple, newer: int, status: str, started: str) -> bool:
    """Whether a start block's _rule lets one of its finished runs go, `newer` of its finished runs being newer."""
    failed, last, since = rule
    if failed and status == "failed":
        return False
    if last is not None:
        return newer >= last
    return since is not None and started < since
