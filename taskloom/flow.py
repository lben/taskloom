"""Flow files: loading, validation and execution order."""

from __future__ import annotations

import datetime as dt
import os
import random
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from . import params as P
from .block import AFTER, DONE, IDENTIFIER, all_ports, fields, setting_label
from .calendars import load_calendar
from .config import keep_runs_for
from .expr import functions
from .schedule import Trigger

FORMAT_VERSION = 1
FLOW_KEYS = {"taskloom", "name", "description", "params", "calendar", "timezone", "triggers", "overlap", "notify",
             "blocks", "edges", "notes"}  # notes: text on the canvas; only the editor uses them
OVERLAP_POLICIES = ("skip", "queue", "allow")
BLOCK_KEYS = {"type", "version", "config", "retry", "timeout", "ui"}
ERROR_PORT = "error"
_EDGE = re.compile(r"^\s*(\w+)\.(\w+)\s*->\s*(\w+)\.(\w+)\s*$")
_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ms|s|m|h)?\s*$")
_UNITS = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, None: 1}
# Said after a problem only blocks that do not take part have: in a run, and in the flow as a whole (they take part in
# no run).
NOT_IN_RUN = "not taking part in this run"
OFF = "off: not connected to a start block"


class FlowError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("\n".join(errors))
        self.errors = errors


def parse_duration(value) -> float:
    match = _DURATION.match(str(value))
    if not match:
        raise ValueError(f"invalid duration {value!r} (examples: 30, 500ms, 30s, 10m, 2h)")
    return float(match.group(1)) * _UNITS[match.group(2)]


@dataclass
class Retry:
    policy: str = "none"
    max_attempts: int = 1
    delay: float = 0.0  # fixed
    initial: float = 1.0  # exponential
    factor: float = 2.0
    max_delay: float = 3600.0
    jitter: float = 0.0  # fraction of each delay removed at random, 0..1

    @classmethod
    def parse(cls, data) -> "Retry":
        if not data:
            return cls()
        if not isinstance(data, dict):
            raise ValueError(f"retry looks like {{policy: fixed, delay: 30s, max_attempts: 3}}, got {data!r}")
        data = dict(data)
        policy = data.pop("policy", "none")
        if policy not in ("none", "fixed", "exponential"):
            raise ValueError(f"retry policy must be none, fixed or exponential, got {policy!r}")
        allowed = {"none": set(), "fixed": {"delay", "max_attempts"},
                   "exponential": {"initial", "factor", "max_delay", "jitter", "max_attempts"}}[policy]
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"unknown retry setting(s) for policy {policy}: {sorted(unknown)}")
        retry = cls(policy=policy)
        if "delay" in data:
            retry.delay = parse_duration(data["delay"])
        for key in ("initial", "max_delay"):
            if key in data:
                setattr(retry, key, parse_duration(data[key]))
        try:
            retry.max_attempts = 1 if policy == "none" else int(data.get("max_attempts", 3))
            retry.factor = float(data.get("factor", retry.factor))
            retry.jitter = float(data.get("jitter", retry.jitter))
            valid = retry.max_attempts >= 1 and retry.factor >= 1 and 0 <= retry.jitter <= 1
        except (TypeError, ValueError):  # not a number, e.g. max_attempts: three
            valid = False
        if not valid:
            raise ValueError("retry needs max_attempts >= 1, factor >= 1 and jitter between 0 and 1")
        return retry

    def delay_after(self, attempt: int) -> float:
        """Seconds to wait after failed attempt number `attempt` (1-based)."""
        if self.policy == "fixed":
            return self.delay
        base = min(self.initial * self.factor ** (attempt - 1), self.max_delay)
        return base * (1 - self.jitter * random.random())


@dataclass
class BlockSpec:
    id: str
    type: str
    version: int | None = None
    config: dict = field(default_factory=dict)
    retry: Retry = field(default_factory=Retry)
    timeout: float | None = None
    ui: dict = field(default_factory=dict)


@dataclass
class Edge:
    src: str
    src_port: str
    dst: str
    dst_port: str

    def __str__(self):
        return f"{self.src}.{self.src_port} -> {self.dst}.{self.dst_port}"


@dataclass
class Flow:
    name: str
    path: Path
    params: dict
    calendar: str | None
    timezone: str | None
    triggers: list[Trigger]
    overlap: str
    notify: dict | None
    blocks: dict[str, BlockSpec]
    edges: list[Edge]


class _FlowLoader(yaml.SafeLoader):
    """YAML as a flow file is read. A name written twice in one place is an error (plain YAML keeps the last one,
    dropping the other without a word), and a name such as yes or off is that name (plain YAML reads it as
    true / false, so a block named yes would not be the 'yes' its connections name)."""

    def construct_mapping(self, node, deep=False):
        lines = {}
        for key_node, _ in node.value:
            if key_node.tag == "tag:yaml.org,2002:bool":
                key_node.tag = "tag:yaml.org,2002:str"
            if isinstance(key_node, yaml.ScalarNode) and key_node.tag != "tag:yaml.org,2002:merge":
                key, line = self.construct_object(key_node), key_node.start_mark.line + 1
                if key in lines:
                    raise yaml.constructor.ConstructorError(
                        None, None, f"'{key}' is written twice, on lines {lines[key]} and {line}")
                lines[key] = line
        return super().construct_mapping(node, deep)


def read_flow_file(path: Path):
    """What the flow file at `path` holds. Raises OSError, or ValueError saying why it is not readable YAML."""
    try:
        return yaml.load(path.read_text(encoding="utf-8"), _FlowLoader)
    except UnicodeDecodeError as e:  # e.g. UTF-16, as Windows PowerShell writes
        raise ValueError(f"not UTF-8 text, save it as UTF-8 ({e})") from None
    except yaml.YAMLError as e:
        raise ValueError(str(e)) from None


def real_path(path) -> Path:
    """The one spelling of a file's path, the one its runs are recorded under, however it was given: absolute, without
    links or "..", each name as on disk. Windows' resolve() gives that; macOS's keeps the case it was given, so a flow
    opened as NIGHTLY.yaml would miss the runs of nightly.yaml. A file not there yet (about to be saved): its folder's
    spelling. One that cannot be opened keeps the spelling given (reading it then fails, and says why)."""
    path = Path(path).resolve()
    if sys.platform != "darwin":
        return path
    import fcntl

    for known in (path, path.parent):
        try:
            fd = os.open(known, os.O_RDONLY)
        except OSError:
            continue
        try:
            real = Path(os.fsdecode(fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(1024)).rstrip(b"\0")))
        finally:
            os.close(fd)
        return real / path.relative_to(known)
    return path


def load_flow(path) -> Flow:
    path = Path(path)
    try:
        data = read_flow_file(path)
    except (OSError, ValueError) as e:
        raise FlowError([f"cannot read {path}: {e}"]) from None
    return flow_from_data(data, path)


def flow_from_data(data, path: Path) -> Flow:
    """A flow from what its file holds (`path`: where it is, for its default name)."""
    if not isinstance(data, dict):
        raise FlowError([f"{path}: expected a mapping at the top level"])
    errors = []
    version = data.get("taskloom", FORMAT_VERSION)
    if not isinstance(version, int):
        errors.append(f"taskloom: {version!r} is not a flow format: write it as a number (this version reads "
                      f"taskloom: {FORMAT_VERSION})")
    elif version != FORMAT_VERSION:
        errors.append(f"unsupported flow format taskloom: {version} (this version reads {FORMAT_VERSION})")
    errors += [f"unknown top-level key '{k}'" for k in data if k not in FLOW_KEYS]

    flow_params = {}
    for name, spec in _mapping(data.get("params"), "params", errors).items():
        if not IDENTIFIER.match(str(name)):
            errors.append(f"params.{name}: not a valid name")
        elif not isinstance(spec, dict) or spec.get("type") not in P.TYPES:
            errors.append(f"params.{name}: needs a type, one of {', '.join(P.TYPES)}")
        elif set(spec) - {"type", "value", "expr"} or ("value" in spec) == ("expr" in spec):
            errors.append(f"params.{name}: needs exactly one of 'value' or 'expr' (and only type/value/expr)")
        else:
            flow_params[name] = spec

    blocks = {}
    for block_id, spec in _mapping(data.get("blocks"), "blocks", errors).items():
        where = f"blocks.{block_id}"
        if not IDENTIFIER.match(str(block_id)) or block_id == ERROR_PORT:
            errors.append(f"{where}: not a valid block id")
            continue
        if not isinstance(spec, dict) or "type" not in spec:
            errors.append(f"{where}: needs a 'type'")
            continue
        errors += [f"{where}: unknown key '{k}'" for k in spec if k not in BLOCK_KEYS]
        if spec.get("version") is not None and not isinstance(spec["version"], int):
            errors.append(f"{where}: version must be a whole number, got {spec['version']!r}")
            continue
        try:
            blocks[block_id] = BlockSpec(
                id=block_id, type=str(spec["type"]), version=spec.get("version"),
                config=dict(_mapping(spec.get("config"), f"{where}.config", errors)),
                retry=Retry.parse(spec.get("retry")),
                timeout=parse_duration(spec["timeout"]) if spec.get("timeout") is not None else None,
                ui=dict(_mapping(spec.get("ui"), f"{where}.ui", errors)),
            )
        except (ValueError, TypeError) as e:
            errors.append(f"{where}: {e}")

    triggers, trigger_errors = schedules_of(data)
    errors += trigger_errors
    notify = data.get("notify")
    if notify is not None:
        if not isinstance(notify, dict) or set(notify) - {"when", "to"}:
            errors.append("notify looks like {when: [failure, success, skipped], to: [you@example.com]}")
        elif set(notify.get("when") or []) - {"failure", "success", "skipped"}:
            errors.append("notify.when can contain failure, success and skipped")
    overlap = data.get("overlap", "skip")
    if overlap not in OVERLAP_POLICIES:
        errors.append(f"overlap must be one of {', '.join(OVERLAP_POLICIES)}")

    edges = []
    for text in data.get("edges") or []:
        match = _EDGE.match(str(text))
        if match:
            edges.append(Edge(*match.groups()))
        else:
            errors.append(f"edges: cannot read {text!r} (expected 'block.port -> block.port')")

    if errors:
        raise FlowError(errors)
    return Flow(
        name=str(data.get("name") or path.stem), path=path, params=flow_params,
        calendar=data.get("calendar"), timezone=data.get("timezone"),
        triggers=triggers, overlap=overlap, notify=notify, blocks=blocks, edges=edges,
    )


def _mapping(value, where: str, errors: list) -> dict:
    """A part of the flow file that holds names, each followed by ':' (left empty: none). Anything else is an
    error, added to `errors`, and read as none."""
    if value and not isinstance(value, dict):
        errors.append(f"{where}: expected a mapping: each name followed by ':' and its value")
        return {}
    return value or {}


def schedules_of(data: dict) -> tuple[list[Trigger], list[str]]:
    """The flow's schedules: active Schedule blocks and the flow-level `triggers:` list."""
    triggers, errors = [], []
    for i, trigger in enumerate(data.get("triggers") or []):
        try:
            triggers.append(Trigger(trigger))
        except ValueError as e:
            errors.append(f"triggers[{i}]: {e}")
    blocks = data.get("blocks")
    for block_id, spec in (blocks.items() if isinstance(blocks, dict) else ()):  # else: flow_from_data says so
        if not isinstance(spec, dict) or spec.get("type") != "trigger.schedule":
            continue
        config = spec.get("config") or {}
        if not isinstance(config, dict) or config.get("active", True) is False:
            continue
        timing = {k: config[k] for k in ("cron", "every", "at", "misfire") if config.get(k) not in (None, "")}
        if len({"cron", "every", "at"} & set(timing)) != 1:
            errors.append(f"blocks.{block_id}: a Schedule block needs either cron (e.g. 0 7 * * MON-FRI), "
                          "every (e.g. 5 minutes) or at (e.g. 2026-10-08 15:30)")
            continue
        try:
            triggers.append(Trigger({"schedule": timing}, block=block_id))
        except ValueError as e:  # e.g. what is wrong with its cron line
            errors.append(f"blocks.{block_id}: {e}")
    return triggers, errors


def block_config(cls, spec: BlockSpec) -> dict:
    """Apply defaults, migrations and type coercion to a block's config."""
    config = dict(spec.config)
    if spec.version is not None and spec.version < cls.version:
        config = cls.migrate(config, spec.version)
    elif spec.version is not None and spec.version > cls.version:
        raise ValueError(f"saved with version {spec.version} of {cls.type_id}, but this install has version {cls.version}")
    unknown = set(config) - set(cls.config)
    if unknown:
        raise ValueError(f"unknown setting(s) {sorted(unknown)}")
    result = {}
    for name, f in cls.config.items():
        if name in config and config[name] is not None:
            try:
                result[name] = f.coerce(config[name])
            except ValueError as e:
                raise ValueError(f"{setting_label(name, f)}: {e}") from None
        elif f.required:
            raise ValueError(f"{setting_label(name, f)} is required")
        else:
            result[name] = f.default
    return result


def keep_source_names(data: dict, registry, moved: dict | None = None, within: set | None = None):
    """Keep each fields.SourceNames setting in a flow file's `data` in step with what is connected into its input,
    after an edit of `data` (made in place): a name follows its output when the edit renamed it (`moved`:
    {"block.port": its new "block.port"}, in the blocks `within`, or all), the names of outputs no longer connected
    there go, and an output connected there without one gets its default (fields.SourceNames.in_effect)."""
    edges = [m.groups() for text in data.get("edges") or [] if (m := _EDGE.match(str(text)))]
    params = data.get("params") or {}
    for block_id, spec in (data.get("blocks") or {}).items():
        cls = registry.get(spec.get("type")) if isinstance(spec, dict) else None
        for key, f in (cls.config.items() if cls else ()):
            config = spec.get("config") or {}
            stored = config.get(key) or {}
            if not isinstance(f, fields.SourceNames) or not isinstance(stored, dict):
                continue  # (not a dict: written wrong by hand, as Properties says)
            if within is None or block_id in within:
                stored = {(moved or {}).get(source, source): name for source, name in stored.items()}
            sources = [f"{s}.{sp}" for s, sp, d, dp in edges if (d, dp) == (block_id, f.port)]
            names = f.in_effect({s: stored[s] for s in sources if s in stored}, sources, params)
            if names != (config.get(key) or {}):
                spec["config"] = {**config, key: names} if names else {k: v for k, v in config.items() if k != key}


def run_clock(flow: Flow, home, as_of: dt.datetime | None = None) -> tuple[dt.datetime, dict]:
    """The run's fixed 'now' (with the flow's time zone, if it has one) and the expression functions
    (calendar applied; their now() is that time without the zone).

    `as_of` replaces 'now', e.g. the time a missed scheduled run was due. Like
    schedules, it is in the machine's local time unless it has a time zone of its
    own (e.g. the time of the run that For Each runs this flow from); with a flow
    time zone it is converted, as the current time is.
    """
    tz = ZoneInfo(flow.timezone) if flow.timezone else None
    if as_of is not None:
        now = as_of.astimezone(tz) if tz or as_of.tzinfo else as_of
    else:
        now = dt.datetime.now(tz)
    calendar = load_calendar(flow.calendar or home.settings()["calendar"], home.calendars_dir)
    return now, functions(now.replace(tzinfo=None), calendar, lambda name: load_calendar(name, home.calendars_dir))


def validate(flow: Flow, registry, home, part: set[str] | None = None) -> tuple[list[str], list[str]]:
    """Return (errors, warnings). A flow with errors cannot run.

    `part`: the blocks of the run it checks the flow for; None: the flow as a whole, whose blocks taking part in some
    run are those a start block's line reaches (all of them, without start blocks). A problem only blocks that do not
    take part have (in their settings, an input not connected, a loop among them) is a warning that says so; the
    others (the flow's connections and parameters, the settings) are errors whatever block they are about."""
    errors, warnings, apart = [], [], []  # apart: the problems of blocks that do not take part
    if part is None:
        on = _taking_part(flow, registry, _start_blocks(flow, registry))
        part, why = (set(flow.blocks) if on is None else on), OFF
    else:
        why = NOT_IN_RUN
    connections = home.connections()
    try:
        now, funcs = run_clock(flow, home)
        calendar_name = flow.calendar or home.settings()["calendar"]
        if calendar_name:
            warnings += load_calendar(calendar_name, home.calendars_dir).warnings_for_year(now.year)
        P.resolve(flow.params, {}, funcs)
        keep_runs_for(home.settings())  # used as a run ends: a mistake there is refused before it starts
    except Exception as e:
        errors.append(str(e))

    ports = {}
    for block_id, spec in flow.blocks.items():
        found = errors if block_id in part else apart
        cls = registry.get(spec.type)
        if cls is None:
            found.append(f"{block_id}: unknown block type '{spec.type}'")
            continue
        try:
            config = block_config(cls, spec)
            inputs, outputs = all_ports(cls, config)
        except Exception as e:
            found.append(f"{block_id}: {e}")
            continue
        ports[block_id] = (inputs, outputs)
        sources = {}
        for edge in flow.edges:
            if edge.dst == block_id:
                sources.setdefault(edge.dst_port, []).append(f"{edge.src}.{edge.src_port}")
        found += [f"{block_id}: {problem}" for problem in cls.problems(config, sources, set(flow.params))]
        has_params_input = any(e.dst == block_id and e.dst_port == "params" for e in flow.edges)
        for name, f in cls.config.items():
            value = config.get(name)
            if not isinstance(value, str):
                continue
            setting = f"{block_id}: {setting_label(name, f)}"
            if f.template:
                try:
                    missing = P.template_names(value) - set(flow.params)
                except ValueError as e:
                    found.append(f"{setting}: {e} (write {{{{ and }}}} for literal braces)")
                    continue
                found += [f"{setting}: unknown parameter {{{m}}}" for m in sorted(missing)]
            if isinstance(f, fields.Code) and f.language == "sql" and not has_params_input:
                missing = set(P.to_qmark(value)[1]) - set(flow.params)
                found += [f"{setting}: unknown bind parameter :{m}" for m in sorted(missing)]
            if isinstance(f, fields.Connection):
                conn = connections.get(value)
                if conn is None:
                    found.append(f"{setting}: no connection named '{value}' in connections.yaml")
                elif conn["kind"] != f.kind:
                    found.append(f"{setting}: connection '{value}' is {conn['kind']}, expected {f.kind}")

    connected = set()
    for edge in flow.edges:
        if edge.src not in flow.blocks or edge.dst not in flow.blocks:
            errors.append(f"edge {edge}: unknown block")
            continue
        if edge.src in ports and edge.src_port not in ports[edge.src][1] and edge.src_port != ERROR_PORT:
            errors.append(f"edge {edge}: '{edge.src}' has no output '{edge.src_port}'")
        if edge.dst in ports and edge.dst_port not in ports[edge.dst][0]:
            errors.append(f"edge {edge}: '{edge.dst}' has no input '{edge.dst_port}'")
        port = ports[edge.dst][0].get(edge.dst_port) if edge.dst in ports else None
        many = getattr(port, "many", False) or getattr(port, "any_of", False)
        if (edge.dst, edge.dst_port) in connected and not many:
            errors.append(f"edge {edge}: input '{edge.dst}.{edge.dst_port}' is already connected")
        connected.add((edge.dst, edge.dst_port))
    for block_id, (inputs, _) in ports.items():
        for name, port in inputs.items():
            if port.required and (block_id, name) not in connected:
                (errors if block_id in part else apart).append(
                    f"blocks.{block_id}: required input '{name}' is not connected")
    warnings += [f"{problem} ({why})" for problem in apart]
    warnings += unconnected_starts(flow, registry)
    if not errors:
        try:
            order(flow)
        except FlowError as e:
            if any(b in part and _leads_back(flow, b) for b in flow.blocks):  # a loop through a block taking part
                errors += e.errors
            else:
                warnings += [f"{problem} ({why})" for problem in e.errors]
    return errors, warnings


def unconnected_starts(flow: Flow, registry) -> list[str]:
    """Start blocks connected to nothing: when one starts the flow, nothing runs."""
    return [f"{block_id} is not connected to anything: nothing runs when it starts the flow (connect its "
            "start to the first block)" for block_id, spec in flow.blocks.items()
            if (cls := registry.get(spec.type)) is not None and cls.is_trigger
            and not any(e.src == block_id for e in flow.edges)]


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


def _start_blocks(flow: Flow, registry) -> set[str]:
    return {b for b, s in flow.blocks.items() if (cls := registry.get(s.type)) is not None and cls.is_trigger}


def order(flow: Flow, part: set[str] | None = None) -> list[str]:
    """Topological order; ties keep the order blocks appear in the file. With `part` (the blocks a run takes part
    in), a loop among the other blocks, which do not run, is left out of it (with its connections)."""
    loops = {b for b in flow.blocks if b not in part and _leads_back(flow, b)} if part is not None else set()
    blocks = [b for b in flow.blocks if b not in loops]
    edges = [e for e in flow.edges if e.src not in loops and e.dst not in loops]
    indegree = {b: 0 for b in blocks}
    for e in edges:
        indegree[e.dst] += 1
    ready = [b for b in blocks if indegree[b] == 0]
    result = []
    while ready:
        block_id = ready.pop(0)
        result.append(block_id)
        for e in edges:
            if e.src == block_id:
                indegree[e.dst] -= 1
                if indegree[e.dst] == 0:
                    ready.append(e.dst)
        ready.sort(key=list(flow.blocks).index)
    if len(result) != len(blocks):  # left out: the blocks in a cycle and those after one
        cyclic = sorted(b for b in blocks if b not in result and _leads_back(flow, b))
        raise FlowError([f"the flow has a cycle through: {', '.join(cyclic)}"])
    return result


def _leads_back(flow: Flow, block_id: str) -> bool:
    """Whether connections lead from the block back to itself."""
    seen, todo = set(), [block_id]
    while todo:
        current = todo.pop()
        for e in flow.edges:
            if e.src == current:
                if e.dst == block_id:
                    return True
                if e.dst not in seen:
                    seen.add(e.dst)
                    todo.append(e.dst)
    return False
