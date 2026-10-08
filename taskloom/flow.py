"""Flow files: loading, validation and execution order."""

from __future__ import annotations

import datetime as dt
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from . import params as P
from .block import IDENTIFIER, fields
from .calendars import load_calendar
from .expr import functions
from .schedule import Trigger

FORMAT_VERSION = 1
FLOW_KEYS = {"taskloom", "name", "description", "params", "calendar", "timezone", "triggers", "overlap", "notify",
             "blocks", "edges"}
OVERLAP_POLICIES = ("skip", "queue", "allow")
BLOCK_KEYS = {"type", "version", "config", "retry", "timeout", "ui"}
ERROR_PORT = "error"
_EDGE = re.compile(r"^\s*(\w+)\.(\w+)\s*->\s*(\w+)\.(\w+)\s*$")
_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ms|s|m|h)?\s*$")
_UNITS = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, None: 1}


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
        data = dict(data)
        policy = data.pop("policy", "none")
        if policy not in ("none", "fixed", "exponential"):
            raise ValueError(f"retry policy must be none, fixed or exponential, got {policy!r}")
        allowed = {"none": set(), "fixed": {"delay", "max_attempts"},
                   "exponential": {"initial", "factor", "max_delay", "jitter", "max_attempts"}}[policy]
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"unknown retry setting(s) for policy {policy}: {sorted(unknown)}")
        retry = cls(policy=policy, max_attempts=1 if policy == "none" else int(data.get("max_attempts", 3)))
        if "delay" in data:
            retry.delay = parse_duration(data["delay"])
        for key in ("initial", "max_delay"):
            if key in data:
                setattr(retry, key, parse_duration(data[key]))
        retry.factor = float(data.get("factor", retry.factor))
        retry.jitter = float(data.get("jitter", retry.jitter))
        if retry.max_attempts < 1 or not 0 <= retry.jitter <= 1 or retry.factor < 1:
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


def load_flow(path) -> Flow:
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise FlowError([f"cannot read {path}: {e}"]) from None
    if not isinstance(data, dict):
        raise FlowError([f"{path}: expected a mapping at the top level"])
    errors = []
    if data.get("taskloom", FORMAT_VERSION) != FORMAT_VERSION:
        errors.append(f"unsupported flow format taskloom: {data.get('taskloom')} (this version reads {FORMAT_VERSION})")
    errors += [f"unknown top-level key '{k}'" for k in data if k not in FLOW_KEYS]

    flow_params = {}
    for name, spec in (data.get("params") or {}).items():
        if not IDENTIFIER.match(str(name)):
            errors.append(f"params.{name}: not a valid name")
        elif not isinstance(spec, dict) or spec.get("type") not in P.TYPES:
            errors.append(f"params.{name}: needs a type, one of {', '.join(P.TYPES)}")
        elif set(spec) - {"type", "value", "expr"} or ("value" in spec) == ("expr" in spec):
            errors.append(f"params.{name}: needs exactly one of 'value' or 'expr' (and only type/value/expr)")
        else:
            flow_params[name] = spec

    blocks = {}
    for block_id, spec in (data.get("blocks") or {}).items():
        where = f"blocks.{block_id}"
        if not IDENTIFIER.match(str(block_id)) or block_id == ERROR_PORT:
            errors.append(f"{where}: not a valid block id")
            continue
        if not isinstance(spec, dict) or "type" not in spec:
            errors.append(f"{where}: needs a 'type'")
            continue
        errors += [f"{where}: unknown key '{k}'" for k in spec if k not in BLOCK_KEYS]
        try:
            blocks[block_id] = BlockSpec(
                id=block_id, type=str(spec["type"]), version=spec.get("version"),
                config=dict(spec.get("config") or {}), retry=Retry.parse(spec.get("retry")),
                timeout=parse_duration(spec["timeout"]) if spec.get("timeout") is not None else None,
                ui=dict(spec.get("ui") or {}),
            )
        except (ValueError, TypeError) as e:
            errors.append(f"{where}: {e}")

    triggers = []
    for i, trigger in enumerate(data.get("triggers") or []):
        try:
            triggers.append(Trigger(trigger))
        except ValueError as e:
            errors.append(f"triggers[{i}]: {e}")
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


def block_config(cls, spec: BlockSpec) -> dict:
    """Apply defaults, migrations and type coercion to a block's config."""
    config = dict(spec.config)
    if spec.version is not None and spec.version < cls.version:
        config = cls.migrate(config, spec.version)
    elif spec.version is not None and spec.version > cls.version:
        raise ValueError(f"saved with version {spec.version} of {cls.type_id}, but this install has version {cls.version}")
    unknown = set(config) - set(cls.config)
    if unknown:
        raise ValueError(f"unknown config key(s) {sorted(unknown)}")
    result = {}
    for name, f in cls.config.items():
        if name in config and config[name] is not None:
            try:
                result[name] = f.coerce(config[name])
            except ValueError as e:
                raise ValueError(f"config.{name}: {e}") from None
        elif f.required:
            raise ValueError(f"config.{name} is required")
        else:
            result[name] = f.default
    return result


def run_clock(flow: Flow, home, as_of: dt.datetime | None = None) -> tuple[dt.datetime, dict]:
    """The run's fixed 'now' and the expression functions (calendar applied).

    `as_of` replaces 'now', e.g. the time a missed scheduled run was due. Like
    schedules, it is in the machine's local time; with a flow time zone it is
    converted, as the current time is.
    """
    tz = ZoneInfo(flow.timezone) if flow.timezone else None
    if as_of is not None:
        now = as_of.astimezone(tz).replace(tzinfo=None) if tz else as_of
    else:
        now = dt.datetime.now(tz).replace(tzinfo=None)
    calendar = load_calendar(flow.calendar or home.settings()["calendar"], home.calendars_dir)
    return now, functions(now, calendar, lambda name: load_calendar(name, home.calendars_dir))


def validate(flow: Flow, registry, home) -> tuple[list[str], list[str]]:
    """Return (errors, warnings). A flow with errors cannot run."""
    errors, warnings = [], []
    connections = home.connections()
    try:
        now, funcs = run_clock(flow, home)
        calendar_name = flow.calendar or home.settings()["calendar"]
        if calendar_name:
            warnings += load_calendar(calendar_name, home.calendars_dir).warnings_for_year(now.year)
        P.resolve(flow.params, {}, funcs)
    except Exception as e:
        errors.append(str(e))

    ports = {}
    for block_id, spec in flow.blocks.items():
        where = f"blocks.{block_id}"
        cls = registry.get(spec.type)
        if cls is None:
            errors.append(f"{where}: unknown block type '{spec.type}'")
            continue
        try:
            config = block_config(cls, spec)
            inputs, outputs = cls.ports(config)
        except Exception as e:
            errors.append(f"{where}: {e}")
            continue
        ports[block_id] = (inputs, outputs)
        has_params_input = any(e.dst == block_id and e.dst_port == "params" for e in flow.edges)
        for name, f in cls.config.items():
            value = config.get(name)
            if not isinstance(value, str):
                continue
            if f.template:
                try:
                    missing = P.template_names(value) - set(flow.params)
                except ValueError as e:
                    errors.append(f"{where}.config.{name}: {e} (write {{{{ and }}}} for literal braces)")
                    continue
                errors += [f"{where}.config.{name}: unknown parameter {{{m}}}" for m in sorted(missing)]
            if isinstance(f, fields.Code) and f.language == "sql" and not has_params_input:
                missing = set(P.to_qmark(value)[1]) - set(flow.params)
                errors += [f"{where}.config.{name}: unknown bind parameter :{m}" for m in sorted(missing)]
            if isinstance(f, fields.Connection):
                conn = connections.get(value)
                if conn is None:
                    errors.append(f"{where}.config.{name}: no connection named '{value}' in connections.yaml")
                elif conn["kind"] != f.kind:
                    errors.append(f"{where}.config.{name}: connection '{value}' is {conn['kind']}, expected {f.kind}")

    connected = set()
    for edge in flow.edges:
        if edge.src not in flow.blocks or edge.dst not in flow.blocks:
            errors.append(f"edge {edge}: unknown block")
            continue
        if edge.src in ports and edge.src_port not in ports[edge.src][1] and edge.src_port != ERROR_PORT:
            errors.append(f"edge {edge}: '{edge.src}' has no output '{edge.src_port}'")
        if edge.dst in ports and edge.dst_port not in ports[edge.dst][0]:
            errors.append(f"edge {edge}: '{edge.dst}' has no input '{edge.dst_port}'")
        many = edge.dst in ports and getattr(ports[edge.dst][0].get(edge.dst_port), "many", False)
        if (edge.dst, edge.dst_port) in connected and not many:
            errors.append(f"edge {edge}: input '{edge.dst}.{edge.dst_port}' is already connected")
        connected.add((edge.dst, edge.dst_port))
    for block_id, (inputs, _) in ports.items():
        for name, port in inputs.items():
            if port.required and (block_id, name) not in connected:
                errors.append(f"blocks.{block_id}: required input '{name}' is not connected")
    if not errors:
        try:
            order(flow)
        except FlowError as e:
            errors += e.errors
    return errors, warnings


def order(flow: Flow) -> list[str]:
    """Topological order; ties keep the order blocks appear in the file."""
    indegree = {b: 0 for b in flow.blocks}
    for e in flow.edges:
        indegree[e.dst] += 1
    ready = [b for b in flow.blocks if indegree[b] == 0]
    result = []
    while ready:
        block_id = ready.pop(0)
        result.append(block_id)
        for e in flow.edges:
            if e.src == block_id:
                indegree[e.dst] -= 1
                if indegree[e.dst] == 0:
                    ready.append(e.dst)
        ready.sort(key=list(flow.blocks).index)
    if len(result) != len(flow.blocks):
        cyclic = sorted(set(flow.blocks) - set(result))
        raise FlowError([f"the flow has a cycle through: {', '.join(cyclic)}"])
    return result
