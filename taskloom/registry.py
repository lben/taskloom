"""Finds block classes: built-ins, installed plugins, then the user blocks folder.

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

BUILTIN_MODULES = ("taskloom.blocks.logic", "taskloom.blocks.data", "taskloom.blocks.database",
                   "taskloom.blocks.files", "taskloom.blocks.servers", "taskloom.blocks.reports")
ENTRY_POINT_GROUP = "taskloom.blocks"


class Registry:
    def __init__(self):
        self.blocks: dict[str, type[Block]] = {}
        self.sources: dict[str, str] = {}  # type_id -> "built-in", "plugin" or a file path
        self.warnings: list[str] = []

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
            if path.name.startswith("_"):
                continue
            module_name = f"taskloom_user_blocks.{path.stem}"
            try:
                spec = importlib.util.spec_from_file_location(module_name, path)
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
            except Exception as e:
                sys.modules.pop(module_name, None)
                registry.warnings.append(f"could not load user block file {path}: {e}")
                continue
            registry._add_from(module, str(path))
    return registry
