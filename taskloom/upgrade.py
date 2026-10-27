"""Checking flows: what this version can fix in them (and fixing it, after a backup), and what needs you.

Fixed automatically:
- an older flow format: each change of the format comes with a step in MIGRATIONS that updates it;
- connections to blocks, inputs or outputs that no longer exist (e.g. after renaming an output);
- the same connection listed twice;
- settings a block no longer has;
- no start block: a flow starts from a Manual Start or a Schedule, so a Manual Start is added, connected to
  its first blocks (it then runs as before: every block).

Anything else the flow's checks find (e.g. a connection name missing from connections.yaml) needs you.
"""

from __future__ import annotations

import copy
import datetime as dt
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .block import all_ports
from .flow import (_EDGE, ERROR_PORT, FORMAT_VERSION, BlockSpec, FlowError, block_config, flow_from_data,
                   read_flow_file, unconnected_starts, validate)

# Format version N -> a function that turns a flow of version N into one of version N + 1.
MIGRATIONS: dict = {}


@dataclass
class Report:
    path: Path
    fixes: list[str] = field(default_factory=list)  # what fixing changes
    problems: list[str] = field(default_factory=list)  # what needs you
    fixed: dict | None = None  # the flow after the fixes (None: nothing to fix)

    @property
    def ok(self) -> bool:
        return not self.fixes and not self.problems


def check(path, registry, home) -> Report:
    """What this version can fix in the flow at `path`, and what needs you. Never raises: a flow it could
    not check is reported, so the other flows are still checked."""
    try:
        return _check(Path(path), registry, home)
    except Exception as e:
        return Report(Path(path), problems=[f"could not check it: {type(e).__name__}: {e}"])


def _check(path: Path, registry, home) -> Report:
    report = Report(path)
    try:
        data = read_flow_file(path)
    except (OSError, ValueError) as e:
        report.problems.append(f"cannot read it: {e}")
        return report
    if not isinstance(data, dict):
        report.problems.append("it is not a flow (expected a mapping at the top level)")
        return report
    new = copy.deepcopy(data)
    version = new.get("taskloom", FORMAT_VERSION)
    if isinstance(version, int) and version > FORMAT_VERSION:
        report.problems.append(f"it was made by a newer Taskloom (flow format {version}; this version knows "
                               f"{FORMAT_VERSION}): update Taskloom")
        return report
    while isinstance(version, int) and version < FORMAT_VERSION:  # not a number: the checks below say so
        if version not in MIGRATIONS:
            report.problems.append(f"this version cannot update flow format {version} (it reads "
                                   f"{FORMAT_VERSION}): fix the file by hand, or ask for help")
            return report
        new = MIGRATIONS[version](new)
        version += 1
        new["taskloom"] = version
        report.fixes.append(f"update the flow format from {version - 1} to {version}")

    blocks = new.get("blocks") if isinstance(new.get("blocks"), dict) else {}
    ports = {}
    for block_id, spec in blocks.items():
        cls = registry.get(spec.get("type")) if isinstance(spec, dict) else None
        if cls is None:
            continue
        config = spec.get("config")
        saved = spec.get("version")
        if isinstance(saved, int) and saved > cls.version:
            continue  # saved by a newer version of the block: the checks below report it; left alone
        if isinstance(saved, int) and saved < cls.version:  # the block's own update of its settings
            try:
                config = spec["config"] = cls.migrate(dict(config or {}), saved)
            except Exception as e:
                report.problems.append(f"{block_id}: cannot update its settings from version {saved}: {e}")
                continue
            spec["version"] = cls.version
            report.fixes.append(f"update the settings of {block_id} from version {saved} to {cls.version} "
                                f"of {cls.title or cls.type_id}")
        if isinstance(config, dict):
            for key in [k for k in config if k not in cls.config]:
                del config[key]
                report.fixes.append(f"remove the setting '{key}' of {block_id}: {cls.title or cls.type_id} "
                                    "blocks no longer have it")
        try:
            ports[block_id] = all_ports(cls, block_config(cls, BlockSpec(
                id=block_id, type=cls.type_id, config=dict(config or {}), version=spec.get("version"))))
        except Exception:
            pass  # its settings have a problem: the checks below report it; its connections are left alone

    edges, seen = [], set()
    for text in new.get("edges") if isinstance(new.get("edges"), list) else []:
        match = _EDGE.match(str(text))
        if match is None:
            edges.append(text)  # cannot be read: reported by the checks below
            continue
        e = match.groups()
        src, src_port, dst, dst_port = e
        why = None
        if src not in blocks or dst not in blocks:
            why = f"there is no block '{src if src not in blocks else dst}'"
        elif src in ports and src_port not in ports[src][1] and src_port != ERROR_PORT:
            why = f"{src} has no output '{src_port}'"
        elif dst in ports and dst_port not in ports[dst][0]:
            why = f"{dst} has no input '{dst_port}'"
        elif e in seen and dst in ports and not _takes_several(ports[dst][0].get(dst_port)):
            # Only where the flow's checks reject it: into an input that takes several, each connection
            # delivers its own copy.
            why = "it is listed twice"
        if why:
            report.fixes.append(f"remove the connection {src}.{src_port} -> {dst}.{dst_port}: {why}")
            continue
        seen.add(e)
        edges.append(text)
    if isinstance(new.get("edges"), list):  # anything else cannot be read: reported, left as it is
        new["edges"] = edges
    if isinstance(new.get("blocks"), dict) and new["blocks"] and not start_blocks(new, registry):
        firsts = first_blocks(new, registry)
        add_manual_start(new, registry)
        report.fixes.append("add a Manual Start connected to its first blocks"
                            + (f" ({', '.join(firsts)})" if firsts else "") + ": a flow starts from a start block")

    try:
        flow = flow_from_data(new, path)
        errors, _ = validate(flow, registry, home)
        errors += unconnected_starts(flow, registry)  # it runs, but does nothing: you meant something else
    except FlowError as e:
        errors = e.errors
    report.problems += errors
    if report.fixes:
        report.fixed = new
    return report


def _takes_several(port) -> bool:
    return bool(getattr(port, "many", False) or getattr(port, "any_of", False))


def start_blocks(data: dict, registry) -> list[str]:
    """The flow's start blocks (Manual Start, Schedule)."""
    return [b for b, spec in (data.get("blocks") or {}).items() if isinstance(spec, dict)
            and (cls := registry.get(spec.get("type"))) is not None and cls.is_trigger]


def first_blocks(data: dict, registry) -> list[str]:
    """The blocks nothing is connected into (where a flow without start blocks begins)."""
    into = {m.group(3) for text in data.get("edges") or [] if (m := _EDGE.match(str(text)))}
    return [b for b in (data.get("blocks") or {}) if b not in into and b not in start_blocks(data, registry)]


def add_manual_start(data: dict, registry) -> str:
    """Add a Manual Start to a flow (left of its first blocks), connected to each of them. Returns its name."""
    blocks, firsts = data.setdefault("blocks", {}), first_blocks(data, registry)
    name, n = "start", 2
    while name in blocks:
        name, n = f"start_{n}", n + 1
    placed = [spec.get("ui") or {} for spec in blocks.values() if isinstance(spec, dict)]
    x = min((ui.get("x", 0) for ui in placed), default=0) - 260
    ys = [(blocks[b].get("ui") or {}).get("y", 0) for b in firsts]
    blocks[name] = {"type": "trigger.manual", "ui": {"x": x, "y": round(sum(ys) / len(ys)) if ys else 0}}
    data.setdefault("edges", [])
    if isinstance(data["edges"], list):
        data["edges"] += [f"{name}.start -> {b}.after" for b in firsts]
    return name


def fix(report: Report, home) -> Path:
    """Back up the flow file (into <Taskloom folder>/backups), then write the fixed flow. Returns the backup."""
    if report.fixed is None:
        raise ValueError(f"{report.path}: nothing to fix")
    backups = home.root / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    backup = backups / f"{report.path.stem}-{dt.datetime.now():%Y%m%d-%H%M%S}{report.path.suffix}"
    n = 2
    while backup.exists():
        backup = backups / f"{report.path.stem}-{dt.datetime.now():%Y%m%d-%H%M%S}-{n}{report.path.suffix}"
        n += 1
    shutil.copy2(report.path, backup)
    tmp = report.path.with_suffix(report.path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(report.fixed, sort_keys=False, allow_unicode=True), encoding="utf-8", newline="\n")
    os.replace(tmp, report.path)  # never a half-written flow
    return backup
