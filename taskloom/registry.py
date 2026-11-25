"""Finds block classes: built-ins, installed plugins, then the user blocks folder; and, for one flow, also its own Local
blocks (for_flow).

Later sources override earlier ones by `type_id`, so a user block can replace a built-in.
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import sys
from importlib.metadata import entry_points
from pathlib import Path

from .block import Block

BUILTIN_MODULES = ("taskloom.blocks.triggers", "taskloom.blocks.logic", "taskloom.blocks.data",
                   "taskloom.blocks.statistics", "taskloom.blocks.anomalies", "taskloom.blocks.checks",
                   "taskloom.blocks.check_table", "taskloom.blocks.database", "taskloom.blocks.files",
                   "taskloom.blocks.servers", "taskloom.blocks.reports")
ENTRY_POINT_GROUP = "taskloom.blocks"
LOCAL = "local."  # the type of a copy of the flow's Local block <name>: local.<name>


class Registry:
    def __init__(self):
        self.blocks: dict[str, type[Block]] = {}
        self.sources: dict[str, str] = {}  # type_id -> "built-in", "plugin", "local" (for_flow) or a file path
        self.warnings: list[str] = []
        self.problems: dict[str, str] = {}  # a Local block with a problem (for_flow): its type_id -> the problem

    def get(self, type_id: str) -> type[Block] | None:
        return self.blocks.get(type_id)

    def _add_from(self, obj, source: str):
        found = [obj] if inspect.isclass(obj) else [
            c for c in vars(obj).values()
            if inspect.isclass(c) and c.__module__ == obj.__name__
        ]
        for cls in found:
            if issubclass(cls, Block) and cls is not Block and cls.type_id:
                if not cls.description:
                    self.warnings.append(f"block {cls.type_id} has no description (shown when hovering over it)")
                self.blocks[cls.type_id] = cls
                self.sources[cls.type_id] = source

    def add_file(self, path: Path):
        """The blocks of a user block file (one that cannot be loaded is a warning)."""
        module_name = f"taskloom_user_blocks.{path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
        except Exception as e:
            sys.modules.pop(module_name, None)
            self.warnings.append(f"could not load user block file {path}: {e}")
            return
        self._add_from(module, str(path))

    def remove_file(self, path: Path):
        """Forget the blocks of a user block file (e.g. it was deleted)."""
        for type_id in [t for t, source in self.sources.items() if source == str(path)]:
            del self.blocks[type_id], self.sources[type_id]


def discover(user_blocks_dir: Path | None) -> Registry:
    registry = Registry()
    for name in BUILTIN_MODULES:
        registry._add_from(importlib.import_module(name), "built-in")
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        try:
            registry._add_from(ep.load(), "plugin")
        except Exception as e:
            registry.warnings.append(f"could not load block plugin {ep.value}: {e}")
    if user_blocks_dir and user_blocks_dir.is_dir():
        for path in sorted(user_blocks_dir.glob("*.py")):
            if not path.name.startswith("_"):
                registry.add_file(path)
    return registry


def for_flow(registry: Registry, local_blocks) -> Registry:
    """The blocks a flow can use: those of `registry` (the blocks folder's, as discover finds them) and the flow's Local
    blocks, from the definitions its file holds (`local_blocks`: {name: definition}), each a block of type
    local.<name> made by the Python Code block (logic.PythonCode.local_block). A definition with a problem is no
    block: its problem is in .problems. A Local block with the name (user.<name>) or the title of a Global block (a
    file in the blocks folder, made later, e.g. from another flow) is a block, its copies still its own, and that
    problem is in .problems too: a Local and a Global block cannot have the same name."""
    python = registry.get("logic.python")
    found = Registry()
    found.warnings = registry.warnings
    for type_id, cls in registry.blocks.items():
        if registry.sources[type_id] != "local":  # (another flow's, if `registry` was one flow's)
            found.blocks[type_id], found.sources[type_id] = cls, registry.sources[type_id]
    global_blocks = {type_id: cls for type_id, cls in found.blocks.items()
                     if found.sources[type_id] not in ("built-in", "plugin")}  # (a file path)
    # (local_blocks not a mapping: the flow's checks say so)
    for name, definition in (local_blocks.items() if isinstance(local_blocks, dict) else ()):
        type_id = f"{LOCAL}{name}"
        try:
            cls = python.local_block(name, definition)
        except ValueError as e:
            found.problems[type_id] = str(e)
            continue
        found.blocks[type_id], found.sources[type_id] = cls, "local"
        same = next((g for g, other in global_blocks.items() if g == f"user.{name}" or other.title == cls.title), None)
        if same is not None:
            found.problems[type_id] = (f"{cls.title} ({type_id}) and the Global block {global_blocks[same].title} "
                                       f"({same}) have the same name: rename one of them")
    return found
