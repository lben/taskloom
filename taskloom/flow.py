"""Flow files: loading, validation and execution order."""

from __future__ import annotations

import datetime as dt
import hashlib
import itertools
import os
import random
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from . import params as P
from .block import AFTER, DONE, IDENTIFIER, all_ports, applies, fields, has_placeholders, setting_label
from .calendars import load_calendar
from .config import keep_runs_for
from .expr import functions
from .schedule import Trigger

FORMAT_VERSION = 1
FLOW_KEYS = {"taskloom", "name", "description", "params", "calendar", "timezone", "triggers", "overlap", "notify",
             "blocks", "edges", "notes"}  # notes: text on the canvas; only the editor uses them
OVERLAP_POLICIES = ("skip", "queue", "allow")
# inside: the Loop (logic.loop) a block is inside, which runs it once per item; ui: where the canvas draws it (a Loop's
# box: also its width and height, once you resized it).
BLOCK_KEYS = {"type", "version", "config", "retry", "timeout", "ui", "inside"}
ERROR_PORT = "error"
_EDGE = re.compile(r"^\s*(\w+)\.(\w+)\s*->\s*(\w+)\.(\w+)\s*$")
_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ms|s|m|h)?\s*$")
_UNITS = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, None: 1}
# Said after a problem only blocks that do not take part have: in a run, and in the flow as a whole (they take part in
# no run).
NOT_IN_RUN = "not taking part in this run"
OFF = "off: not connected to a start block"
# The Splitter and the Merger (blocks/logic.py): each connection leaving a Splitter's `lines` output starts a line,
# which the Merger joins. How the Splitter runs its lines: its settings' keys and Lines run's choices.
SPLITTER, MERGER, LINES = "logic.splitter", "logic.merger", "lines"
LINES_RUN, AT_MOST = "lines_run", "at_most"
SAME_TIME, ONE_AFTER_ANOTHER = "at the same time", "one after another"
# The Loop (blocks/logic.py): a box on the canvas around blocks (those whose `inside` names it), which run once for
# every item arriving at its `items`, getting the item and its index from its `item` and `index`; what reaches its
# `result` is each item's, collected, in the items' order, in its `results`. Its setting At the same time.
LOOP, ITEMS, ITEM, INDEX, RESULT, RESULTS = "logic.loop", "items", "item", "index", "result", "results"
AT_ONCE = "at_the_same_time"
# The Merger's setting Passes on: all lines together (as it is added: it joins them) or each line as it finishes (the
# blocks after it run once per line, see per_line; At the same time, AT_ONCE, of those runs at once).
PASSES_ON, ALL_LINES, EACH_LINE = "passes_on", "all lines together", "each line as it finishes"
RUNS_AGAIN, LINE_ORDER, AS_FINISHED = "runs_again_in", "line order", "the order the lines finished"


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
    inside: str | None = None  # the Loop it is inside


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
    version: str | None = None  # the Git blob id of the bytes it was loaded from (load_flow)


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
    return flow_file_data(path.read_bytes())


def flow_file_data(raw: bytes):
    """What a flow file holding the bytes `raw` holds. Raises ValueError saying why it is not readable YAML."""
    try:
        return yaml.load(raw.decode("utf-8"), _FlowLoader)  # (YAML reads CRLF line ends as LF ones)
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


def version_id(raw: bytes) -> str:
    """The version of a flow file holding the bytes `raw`: their Git blob id, as Git computes it."""
    return hashlib.sha1(b"blob %d\0" % len(raw) + raw).hexdigest()


def load_flow(path) -> Flow:
    """The flow in the file at `path`, with the version of the bytes it was read from."""
    path = Path(path)
    try:
        raw = path.read_bytes()
        data = flow_file_data(raw)
    except (OSError, ValueError) as e:
        raise FlowError([f"cannot read {path}: {e}"]) from None
    flow = flow_from_data(data, path)
    flow.version = version_id(raw)
    return flow


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
        inside = spec.get("inside")
        if inside is not None and not (isinstance(inside, str) and IDENTIFIER.match(inside)):
            errors.append(f"{where}: inside must be the name of the Loop it is inside, got {inside!r}")
            continue
        try:
            blocks[block_id] = BlockSpec(
                id=block_id, type=str(spec["type"]), version=spec.get("version"),
                config=dict(_mapping(spec.get("config"), f"{where}.config", errors)),
                retry=Retry.parse(spec.get("retry")),
                timeout=parse_duration(spec["timeout"]) if spec.get("timeout") is not None else None,
                ui=dict(_mapping(spec.get("ui"), f"{where}.ui", errors)), inside=inside,
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
    """Apply defaults, migrations and type coercion to a block's config. A setting that is not for it (block.applies)
    gets its default; required code that is only comments is as if left empty (only_comments)."""
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
        if not applies(cls, name, config):
            result[name] = f.default  # (a value written for it is not used)
        elif f.required and (config.get(name) is None or only_comments(f, config[name])):
            raise ValueError(f"{setting_label(name, f)} is required")
        elif name in config and config[name] is not None:
            try:
                result[name] = f.coerce(config[name])
            except ValueError as e:
                raise ValueError(f"{setting_label(name, f)}: {e}") from None
        else:
            result[name] = f.default
    return result


def placeholder_problems(f: fields.Field, text: str, params, inputs) -> list[str]:
    """What is wrong with the {name} placeholders in `text`, the value (or an item) of the templated setting `f` of a
    block with the input ports `inputs`: each must name a flow parameter (`params`: their names) or, but in SQL code,
    one of the block's data inputs (not its run after), and not both."""
    try:
        names = P.template_names(text)
    except ValueError as e:
        return [f"{e} (write {{{{ and }}}} for literal braces)"]
    if isinstance(f, fields.Code):  # SQL: flow parameters only
        return [f"unknown parameter {{{n}}}" for n in sorted(names - set(params))]
    data = set(inputs) - {AFTER}
    return [f"{{{n}}} could be the parameter {n} or the input {n}: rename one" if n in params
            else f"{{{n}}} is neither a flow parameter nor an input of this block"
            for n in sorted(names) if (n in params) == (n in data)]


def only_comments(f: fields.Field, value) -> bool:
    """Whether a code setting's value is only comments and blank lines: SQL's -- and /* */, or a Python expression's
    lines starting with #. Not a script's (fields.Code.script), which runs as it is."""
    if not isinstance(f, fields.Code) or f.script or not isinstance(value, str):
        return False
    if f.language == "sql":
        return not P.without_comments(value).strip()
    if f.language in ("python", "expression"):
        return all(line.lstrip().startswith("#") for line in value.splitlines() if line.strip())
    return False


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
    own; with a flow time zone it is converted, as the current time is.
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
            setting = f"{block_id}: {setting_label(name, f)}"
            if f.template:
                found += [f"{setting}: {problem}" for text in (value if isinstance(value, list) else [value])
                          if isinstance(text, str) for problem in placeholder_problems(f, text, flow.params, inputs)]
            if not isinstance(value, str):
                continue
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
    loop_errors, loop_warnings = _loop_problems(flow, registry)
    errors += loop_errors
    warnings += loop_warnings
    if not errors:
        try:
            order(flow)
        except FlowError as e:
            if any(b in part and _leads_back(flow, b) for b in flow.blocks):  # a loop through a block taking part
                errors += e.errors
            else:
                warnings += [f"{problem} ({why})" for problem in e.errors]
    if not errors:
        never, few = _lines_problems(flow, registry, part)
        errors += never
        warnings += few
        never, few = _per_line_problems(flow, registry, part)
        errors += never
        warnings += few
    return errors, warnings


def containers(flow: Flow, block: str) -> list[str]:
    """The Loops `block` is inside, the innermost first (they end at a name that is no block: Validate refuses it)."""
    found, loop = [], flow.blocks[block].inside
    while loop is not None and loop in flow.blocks and loop not in found:
        found.append(loop)
        loop = flow.blocks[loop].inside
    return found


def at_level(flow: Flow, block: str, loop: str | None) -> str | None:
    """`block`, or the Loop it is inside, that is directly inside `loop` (None: in the flow itself, inside no Loop);
    None when `block` is not inside `loop`."""
    for b in [block, *containers(flow, block)]:
        if flow.blocks[b].inside == loop:
            return b
    return None


def from_inside(flow: Flow, edge: Edge) -> bool:
    """Whether a connection goes out of a Loop into the Loop itself: into its result, from a block inside it."""
    return edge.src in flow.blocks and edge.dst in containers(flow, edge.src)


def _level_pairs(flow: Flow) -> list[tuple[str, str]]:
    """The connections as they set the order: each between the two blocks (or Loops) it joins at the level where both
    are. One from outside a Loop into a block inside it orders the Loop after its source (the Loop runs its blocks).
    A Loop's item and index into its blocks, and its blocks into its result, are inside it: they order nothing."""
    pairs = []
    for e in flow.edges:
        if e.src not in flow.blocks or e.dst not in flow.blocks:
            continue
        src_in, dst_in = containers(flow, e.src), containers(flow, e.dst)
        if e.src in dst_in or e.dst in src_in:
            continue
        common = next((c for c in src_in if c in dst_in), None)  # the innermost Loop both are inside
        a, b = at_level(flow, e.src, common), at_level(flow, e.dst, common)
        if a is not None and b is not None:
            pairs.append((a, b))
    return pairs


def _loop_problems(flow: Flow, registry) -> tuple[list[str], list[str]]:
    """(what is wrong with the Loops and the blocks inside them: a block inside something that is not a Loop of the
    flow, a connection out of a Loop but through its result, a Loop's item, index or result used outside it or its
    results inside it, a start block inside one, a Loop's retry or timeout; what to look at: a Loop inside another
    (its blocks run items × items times), a Loop's results connected but its result not, a file path a block inside a
    Loop writes to without a {placeholder}, as every item writes to it)."""
    errors, warnings = [], []
    loops = {b for b, spec in flow.blocks.items() if spec.type == LOOP}
    for block_id, spec in flow.blocks.items():
        if spec.inside is not None and spec.inside not in loops:
            errors.append(f"{block_id}: inside: {spec.inside} is not a Loop of this flow")
        elif block_id in containers(flow, block_id):
            errors.append(f"{block_id}: inside: it is inside itself (through {', '.join(containers(flow, block_id))})")
    if errors:
        return errors, warnings
    for e in flow.edges:
        if e.src not in flow.blocks or e.dst not in flow.blocks:
            continue  # (an unknown block, which validate names)
        src_in, dst_in = containers(flow, e.src), containers(flow, e.dst)
        if flow.blocks[e.dst].type == LOOP and e.dst_port == RESULT:
            if flow.blocks[e.src].inside != e.dst:
                inner = next((c for c in src_in if flow.blocks[c].inside == e.dst), None)
                errors.append(f"edge {e}: {e.dst}.result takes what each item gives back, from a block inside "
                              + (f"{e.dst} itself ({e.src} is inside {inner}: connect it to {inner}.result, and "
                                 f"{inner}.results to {e.dst}.result)" if inner else
                                 f"{e.dst}: connect a block inside the loop to it"))
        elif flow.blocks[e.src].type == LOOP and e.src_port in (ITEM, INDEX):
            if e.src not in dst_in:
                errors.append(f"edge {e}: {e.src}.{e.src_port} is for the blocks inside {e.src}: connect it to a "
                              "block inside the loop")
        elif e.src in dst_in:
            errors.append(f"edge {e}: {e.src}.{e.src_port} comes once all the items of {e.src} have run, so no block "
                          f"inside it can take it (inside, take {e.src}.item or {e.src}.index)")
        elif leaving := [c for c in src_in if c not in dst_in]:
            errors.append(f"edge {e}: from inside the loop {leaving[0]} to outside it: a block inside a loop connects "
                          f"out of it only through the loop's result (connect it to {leaving[0]}.result, and take "
                          f"{leaving[0]}.results outside the loop)")
    for block_id, spec in flow.blocks.items():
        around = containers(flow, block_id)
        if spec.type == LOOP:
            if spec.retry.policy != "none" or spec.timeout is not None:
                errors.append(f"{block_id}: a Loop has no retry or timeout of its own: set them on the blocks inside "
                              "it")
            if around:
                warnings.append(f"{block_id}: a loop inside the loop {around[0]}: its blocks run for every item of "
                                f"{block_id} once per item of {around[0]} (items × items times)")
            if (any(e.src == block_id and e.src_port == RESULTS for e in flow.edges)
                    and not any(e.dst == block_id and e.dst_port == RESULT for e in flow.edges)):
                warnings.append(f"{block_id}: its results is connected, but nothing is connected to its result inside "
                                f"it: every item's result is None (connect to {block_id}.result the block inside that "
                                "gives what each item gives back)")
        cls = registry.get(spec.type)
        if cls is None or not around:
            continue
        if cls.is_trigger:
            errors.append(f"{block_id}: a start block cannot be inside a Loop (it is inside {around[0]})")
        for key, f in cls.config.items():
            value = spec.config.get(key)
            if (f.writes and isinstance(value, str) and value and not has_placeholders(value)
                    and applies(cls, key, spec.config)):
                warnings.append(f"{block_id}: {setting_label(key, f)}: every item of the loop {around[0]} writes to "
                                f"{value} (it has no {{placeholder}}, so each item writes over what the one before "
                                "wrote)")
    return errors, warnings


def _lines_problems(flow: Flow, registry, part: set[str]) -> tuple[list[str], list[str]]:
    """(the layouts of Splitters' lines that could never finish, or that are not supported yet; a Splitter with fewer
    than two lines and a Merger with fewer than two connections). A line waits for what a connection across brings it:
    when not all of its lines can run at once, a connection from a later line into an earlier one makes the earlier
    line wait for a line that cannot start until it has ended; the lines of Splitters side by side that feed each
    other both ways can wait for each other forever."""
    never, few = [], []
    lines = splitter_lines(flow)
    owners: dict[str, dict[str, int]] = {}  # each block of a line: {the Splitter whose line it is in: that line}
    for splitter, its_lines in lines.items():
        for i, (_, blocks) in enumerate(its_lines):
            for block in sorted(blocks & part):
                owners.setdefault(block, {}).setdefault(splitter, i)
    owner = {block: next(iter(its)) for block, its in owners.items() if len(its) == 1}
    for block, its in owners.items():
        if flow.blocks[block].type == SPLITTER:
            outer, i = next(iter(its.items()))
            never.append(f"{block}: a Splitter inside line {i + 1} of {outer}: a Splitter inside another Splitter's "
                         f"line is not supported yet (put it after {outer}'s Merger)")
            continue
        into = [e for e in flow.edges if e.dst == block and e.src in part]
        sides = {s: [str(e) for e in into if owner.get(e.src) == s or e.src == s] for s in its}
        if sum(bool(edges) for edges in sides.values()) > 1:
            never.append(f"{block} takes from the lines of Splitters side by side: " + ", ".join(
                f"{s} (edge {', '.join(edges)})" for s, edges in sides.items() if edges) + " (connect into the first "
                "block of a line, or a Merger's output, instead)")
    # One Splitter's lines wait for another's when a connection into its lines brings what the other's make (also
    # through blocks after the other's Merger): Splitters waiting for each other in a circle could do so forever.
    arcs = {}
    for e in flow.edges:
        to = owner.get(e.dst)
        if to is not None and e.src in part and owner.get(e.src) != to:
            for s in sorted(_lines_before(flow, e.src, owner) - {to}):
                arcs.setdefault((s, to), e)
    circles = []
    for (a, b), e in arcs.items():
        path = _arc_path(arcs, b, a)
        if path and set(path) not in circles:
            circles.append(set(path))
            edges = [e, *(arcs[(x, y)] for x, y in zip(path, path[1:]))]
            never.append(f"the lines of {' and '.join(dict.fromkeys(path))} feed each other both ways ("
                         + ", ".join(f"edge {edge}" for edge in edges) + "), so they could wait for each other "
                         "forever (connect the lines of Splitters side by side one way only, or a Merger's output)")
    runs = line_runs(flow, part, lines)
    for splitter, its_lines in lines.items():
        if len(its_lines) < 2:
            few.append(f"{splitter}: a Splitter with {len(its_lines)} line{'s' if len(its_lines) != 1 else ''} (each "
                       "connection leaving its lines output starts one; it is for running two or more)")
        mode, at_most = lines_run(flow, registry, splitter)
        if mode == SAME_TIME and (at_most is None or at_most >= len(its_lines)):
            continue
        how = ("runs its lines one after another" if mode == ONE_AFTER_ANOTHER
               else f"runs at most {at_most} of its {len(its_lines)} lines at once")
        for e in flow.edges:
            src, dst = (runs.get(b, (None, None)) if b in part else (None, None) for b in (e.src, e.dst))
            if src[0] == dst[0] == splitter and src[1] > dst[1]:
                never.append(f"edge {e}: from line {src[1] + 1} of {splitter} into line {dst[1] + 1}, which would "
                             f"wait for it forever: {splitter} {how} (connect a line only into a later one, or run all "
                             "its lines at once)")
    sections = per_line(flow, registry)
    for block_id, spec in flow.blocks.items():
        joined = [e for e in flow.edges if e.dst == block_id and e.dst_port == LINES]
        closes = any(e.src in blocks and block_id not in blocks for e in joined for blocks in sections.values())
        if spec.type == MERGER and len(joined) < 2 and not closes:  # closing per-line runs, one joins them all
            few.append(f"{block_id}: a Merger with {len(joined)} connection{'s' if len(joined) != 1 else ''} into its "
                       "lines input (it joins two or more lines)")
    return never, few


def _lines_before(flow: Flow, block: str, owner: dict) -> set[str]:
    """The Splitters whose lines `block` is in or comes after."""
    found, seen, todo = set(), set(), [block]
    while todo:
        b = todo.pop()
        if b not in seen:
            seen.add(b)
            found.update([owner[b]] if b in owner else [])
            todo += [e.src for e in flow.edges if e.dst == b and not from_inside(flow, e)]
    return found


def _arc_path(arcs: dict, start: str, goal: str) -> list[str] | None:
    """The Splitters from `start` to `goal` along `arcs` ({(from, to): ...}), both included; None if there is none."""
    paths, seen = [[start]], {start}
    while paths:
        path = paths.pop(0)
        if path[-1] == goal:
            return path
        for a, b in arcs:
            if a == path[-1] and b not in seen:
                seen.add(b)
                paths.append(path + [b])
    return None


def splitter_lines(flow: Flow) -> dict[str, list[tuple[str, set[str]]]]:
    """Each Splitter's lines, in line order: (the block its connection goes to, the blocks of the line: that block and
    the blocks after it, up to the Merger that joins the lines and the first blocks of other lines, its own or another
    Splitter's). The Merger and what follows it, even a block also taking a line's output, are after the lines; a
    block after several of its lines is in each of them (line_runs says which runs it). Line order: top to bottom by
    where each line's first block is on the canvas, then left to right."""
    firsts = {}
    for splitter, spec in flow.blocks.items():
        if spec.type == SPLITTER:
            its = list(dict.fromkeys(e.dst for e in flow.edges if e.src == splitter and e.src_port == LINES
                                     and e.dst in flow.blocks))
            firsts[splitter] = sorted(its, key=lambda b: (flow.blocks[b].ui.get("y", 0), flow.blocks[b].ui.get("x", 0)))
    starts = {b for its in firsts.values() for b in its}
    found = {}
    for splitter, its in firsts.items():
        reached = [_line_blocks(flow, first, starts) for first in its]
        after = set().union(*(joins for _, joins in reached))  # the Mergers joining the lines, and what follows them
        todo = list(after)
        while todo:
            block = todo.pop()
            for e in flow.edges:
                if e.src == block and e.dst in flow.blocks and e.dst not in after and not from_inside(flow, e):
                    after.add(e.dst)
                    todo.append(e.dst)
        found[splitter] = [(first, blocks - after) for first, (blocks, _) in zip(its, reached)]
    return found


def line_runs(flow: Flow, part: set[str], lines: dict | None = None) -> dict[str, tuple[str, int]]:
    """Which line runs each block of a Splitter's lines (splitter_lines): (the Splitter, the line's index). A Splitter
    with one line taking part in the run (`part`) runs it with the blocks around it, as a flow without one: it has
    none here. A block in several of its lines runs in the last of them in line order: once the lines before it are
    done with, what they bring it has arrived. (Validate refuses a block in the lines of two Splitters.)"""
    lines = splitter_lines(flow) if lines is None else lines
    found = {}
    for splitter, its in lines.items():
        if sum(bool(blocks & part) for _, blocks in its) > 1:
            for i, (_, blocks) in reversed(list(enumerate(its))):
                for block in blocks:
                    found.setdefault(block, (splitter, i))
    return found


def _line_blocks(flow: Flow, first: str, starts: set[str]) -> tuple[set[str], set[str]]:
    """(the blocks from `first` on, up to the Mergers and the first blocks of other lines; those Mergers)."""
    blocks, joins, todo = set(), set(), [first]
    while todo:
        block = todo.pop()
        if block in blocks or block in joins or (block in starts and block != first):
            continue
        if flow.blocks[block].type == MERGER:  # it, and what follows it, come after the lines
            joins.add(block)
            continue
        blocks.add(block)
        todo += [e.dst for e in flow.edges if e.src == block and e.dst in flow.blocks and not from_inside(flow, e)]
    return blocks, joins


def lines_run(flow: Flow, registry, splitter: str) -> tuple[str, int | None]:
    """How a Splitter runs its lines: at the same time or one after another, and its At most at once (None: all
    lines at once). While its settings cannot be read, as when it is added (validate says what is wrong)."""
    try:
        config = block_config(registry.get(SPLITTER), flow.blocks[splitter])
        mode, at_most = config[LINES_RUN], config[AT_MOST]
    except Exception:
        return SAME_TIME, None
    return mode, at_most if isinstance(at_most, int) else None


def passes_on(flow: Flow, registry, merger: str) -> str:
    """How a Merger passes on its lines: all lines together, or each line as it finishes. All lines together while its
    settings cannot be read (validate says what is wrong)."""
    try:
        return block_config(registry.get(MERGER), flow.blocks[merger])[PASSES_ON]
    except Exception:
        return ALL_LINES


def per_line(flow: Flow, registry) -> dict[str, set[str]]:
    """Each Merger set to each line as it finishes: the blocks it runs once for each of its lines. They are the blocks
    after it (not after its error output; with the blocks inside a Loop among them), up to the end of the flow or up to
    a Merger set to all lines together, which closes its runs, unless it joins the lines of a Splitter among them. Those
    of one in the runs of another are also that one's."""
    joins: dict[str, set[str]] = {}  # each block taking a line's output at its lines: those lines' Splitters
    for splitter, its in splitter_lines(flow).items():
        for e in flow.edges:
            if e.dst_port == LINES and any(e.src in blocks for _, blocks in its):
                joins.setdefault(e.dst, set()).add(splitter)
    found = {}
    for merger, spec in flow.blocks.items():
        if spec.type != MERGER or passes_on(flow, registry, merger) != EACH_LINE:
            continue

        def after(block: str) -> list[str]:  # (at the Merger's level: into a block inside a Loop, the Loop)
            return [at_level(flow, e.dst, spec.inside) for e in flow.edges
                    if e.src in flow.blocks and e.dst in flow.blocks and at_level(flow, e.src, spec.inside) == block
                    and not from_inside(flow, e) and not (block == merger and e.src_port == ERROR_PORT)]

        section, todo = set(), after(merger)
        while todo:
            block = todo.pop()
            if block is None or block in section or block == merger:
                continue
            if (flow.blocks[block].type == MERGER and passes_on(flow, registry, block) == ALL_LINES
                    and not joins.get(block, set()) & section):
                continue  # it closes the runs
            section.add(block)
            todo += after(block)
        found[merger] = section | {b for b in flow.blocks if set(containers(flow, b)) & section}
    return found


def per_line_of(sections: dict[str, set[str]], block: str) -> str | None:
    """The Merger set to each line as it finishes that runs `block` once per line (`sections`: per_line's): the
    outermost, when its runs hold another's. None if none does."""
    owners = [m for m, blocks in sections.items() if block in blocks]
    return next((m for m in owners if not any(m in sections[o] for o in owners)), None)


def _per_line_problems(flow: Flow, registry, part: set[str]) -> tuple[list[str], list[str]]:
    """(a block run once per line by a Merger set to each line as it finishes that takes from one of its lines' last
    blocks, which may not have ended as it runs; a file path such a block writes to without a {placeholder}, as each
    line's run writes to it)."""
    errors, warnings = [], []
    sections = per_line(flow, registry)
    for m1, m2 in itertools.combinations(sorted(m for m in sections if m in part), 2):
        if m1 in sections[m2] or m2 in sections[m1]:
            continue  # (one runs inside the other's runs)
        level = flow.blocks[m1].inside
        if flow.blocks[m2].inside != level:
            continue
        for block in sorted(b for b in sections[m1] & sections[m2] & part if flow.blocks[b].inside == level):
            into = [e for e in flow.edges if e.src in flow.blocks and e.dst in flow.blocks and not from_inside(flow, e)
                    and at_level(flow, e.dst, level) == block and at_level(flow, e.src, level) != block]
            one, other = ([str(e) for e in into if at_level(flow, e.src, level) in (sections[a] | {a}) - sections[b]]
                          for a, b in ((m1, m2), (m2, m1)))
            if one and other:  # (where their runs meet: the blocks after it are in both only through it)
                errors.append(f"{block} would run once per line of {m1} (edge {', '.join(one)}) and once per line of "
                              f"{m2} (edge {', '.join(other)}), both set to each line as it finishes: a block runs once "
                              "per line of one of them only (close one's runs with a Merger set to all lines together "
                              "first)")
    for merger, blocks in sections.items():
        if merger not in part:
            continue
        # A block after a Merger closing the runs runs once, after them all: what one run has reaches it only through
        # that Merger.
        closers = {e.dst for e in flow.edges if e.src in blocks and e.dst in flow.blocks and e.dst not in blocks
                   and flow.blocks[e.dst].type == MERGER and passes_on(flow, registry, e.dst) == ALL_LINES}
        later, todo = set(), list(closers)
        while todo:
            block = todo.pop()
            for e in flow.edges:
                if e.src == block and e.dst in flow.blocks and e.dst not in later and not from_inside(flow, e):
                    later.add(e.dst)
                    todo.append(e.dst)
        errors += [f"edge {e}: {e.dst} runs once, after {' and '.join(sorted(closers))}, which closes the runs "
                   f"{merger} makes once per line, but takes from {e.src}, which has one line's run: what each run "
                   f"has reaches it only through {' and '.join(sorted(closers))} (connect its output instead)"
                   for e in flow.edges if e.dst in later & blocks & part and e.src in (blocks - later) | {merger}]
        lasts = {e.src for e in flow.edges if e.dst == merger and e.dst_port == LINES}
        errors += [f"edge {e}: {e.dst} runs once per line of {merger} (each line as it finishes), but takes from "
                   f"{e.src}, the end of one of its lines, which may not have ended as it runs (take that line's "
                   f"output from {merger} instead)" for e in flow.edges if e.dst in blocks & part and e.src in lasts]
        for block in sorted(blocks & part):
            spec, cls = flow.blocks[block], registry.get(flow.blocks[block].type)
            for key, f in (cls.config.items() if cls else ()):
                value = spec.config.get(key)
                if (f.writes and isinstance(value, str) and value and not has_placeholders(value)
                        and applies(cls, key, spec.config)):
                    warnings.append(f"{block}: {setting_label(key, f)}: every line of {merger} writes to {value} (it "
                                    "has no {placeholder}, so each line's run writes over what the one before wrote)")
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
    """Topological order (the blocks inside a Loop among themselves, see _level_pairs); ties keep the order blocks
    appear in the file. With `part` (the blocks a run takes part in), a cycle among the other blocks, which do not run,
    is left out of it (with its connections)."""
    pairs = _level_pairs(flow)
    circles = {b for b in flow.blocks if b not in part and _leads_back(flow, b, pairs)} if part is not None else set()
    blocks = [b for b in flow.blocks if b not in circles]
    pairs = [(a, b) for a, b in pairs if a not in circles and b not in circles]
    indegree = {b: 0 for b in blocks}
    for _, b in pairs:
        indegree[b] += 1
    ready = [b for b in blocks if indegree[b] == 0]
    result = []
    while ready:
        block_id = ready.pop(0)
        result.append(block_id)
        for a, b in pairs:
            if a == block_id:
                indegree[b] -= 1
                if indegree[b] == 0:
                    ready.append(b)
        ready.sort(key=list(flow.blocks).index)
    if len(result) != len(blocks):  # left out: the blocks in a cycle and those after one
        cyclic = sorted(b for b in blocks if b not in result and _leads_back(flow, b, pairs))
        raise FlowError([f"the flow has a cycle through: {', '.join(cyclic)}"])
    return result


def _leads_back(flow: Flow, block_id: str, pairs: list | None = None) -> bool:
    """Whether connections lead from the block back to itself (as they set the order: _level_pairs)."""
    pairs = _level_pairs(flow) if pairs is None else pairs
    seen, todo = set(), [block_id]
    while todo:
        current = todo.pop()
        for a, b in pairs:
            if a == current:
                if b == block_id:
                    return True
                if b not in seen:
                    seen.add(b)
                    todo.append(b)
    return False
