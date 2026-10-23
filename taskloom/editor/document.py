"""The flow being edited: the YAML data, saving, and undo/redo."""

from __future__ import annotations

import copy
import re
from pathlib import Path

import yaml
from PySide6.QtCore import QObject, Signal

from ..block import IDENTIFIER
from ..engine import lines as start_lines
from ..flow import _EDGE, FlowError, flow_from_data, read_flow_file

UNDO_LIMIT = 200


DEFAULT_NAME = "untitled"  # a new flow's, until it is saved as a file


def new_flow_data(name: str = DEFAULT_NAME) -> dict:
    return {"taskloom": 1, "name": name, "params": {}, "blocks": {}, "edges": []}


def parse_edge(text: str) -> tuple[str, str, str, str] | None:
    match = _EDGE.match(text)
    return match.groups() if match else None


def edge_text(src, src_port, dst, dst_port) -> str:
    return f"{src}.{src_port} -> {dst}.{dst_port}"


class FlowDocument(QObject):
    """Every change goes through a method here so it can be undone and saved."""

    changed = Signal()

    def __init__(self, data: dict | None = None, path: Path | None = None):
        super().__init__()
        self.data = data if data is not None else new_flow_data()
        self._fill_in_lists()
        self.path = path
        self.dirty = False
        self.renamed: dict[str, str] = {}  # old block name -> new, until the canvas has followed it
        self._place_unpositioned()
        self._undo: list[dict] = []
        self._redo: list[dict] = []

    def _fill_in_lists(self):
        """`params:`, `blocks:` or `edges:` with nothing after it (YAML reads None): none, as the runner reads it."""
        for key, empty in (("params", {}), ("blocks", {}), ("edges", [])):
            self.data[key] = self.data.get(key) or empty

    def _place_unpositioned(self):
        """Give blocks without a saved position one, in columns by how far along the flow they are."""
        missing = [b for b, spec in self.blocks.items() if not (spec.get("ui") or {}).keys() >= {"x", "y"}]
        if not missing:
            return
        depth = {b: 0 for b in self.blocks}
        for _ in range(len(self.blocks)):
            for src, _, dst, _ in self.edges():
                if src in depth and dst in depth and depth[dst] <= depth[src]:
                    depth[dst] = depth[src] + 1
        rows: dict[int, int] = {}
        for block_id in missing:
            d = depth[block_id]
            row = rows[d] = rows.get(d, -1) + 1
            self.blocks[block_id]["ui"] = {"x": d * 250, "y": row * 150}

    @classmethod
    def load(cls, path: Path) -> "FlowDocument":
        data = read_flow_file(Path(path)) or {}
        if not isinstance(data, dict):
            raise ValueError(f"{path}: not a flow file")
        return cls(data, Path(path))

    def save(self, path: Path | None = None):
        path = Path(path or self.path)
        data = self.data
        if path != self.path and data.get("name") == DEFAULT_NAME:  # saved as a file (first time, Save as): its name
            data = {**data, "name": path.stem}
        path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
        self.data, self.path = data, path  # only once written: a file that cannot be written leaves the flow as it was
        self.dirty = False
        self.changed.emit()

    @property
    def title(self) -> str:
        return self.path.name if self.path else f"{self.data.get('name') or 'untitled'} (unsaved)"

    # --- undo ------------------------------------------------------------------

    def _change(self):
        """Call before every modification."""
        self._undo.append(copy.deepcopy(self.data))
        del self._undo[:-UNDO_LIMIT]
        self._redo.clear()

    def _done(self):
        self.dirty = True
        self.changed.emit()

    def undo(self):
        if self._undo:
            self._redo.append(self.data)
            self.data = self._undo.pop()
            self._done()

    def redo(self):
        if self._redo:
            self._undo.append(self.data)
            self.data = self._redo.pop()
            self._done()

    # --- reading -----------------------------------------------------------------

    @property
    def blocks(self) -> dict:
        return self.data["blocks"]

    def edges(self) -> list[tuple[str, str, str, str]]:
        return [e for e in (parse_edge(t) for t in self.data["edges"]) if e]

    def lines(self, registry) -> dict[str, set[str]]:
        """Each start block's line as the flow is now: the blocks its runs take part in ({} while it cannot be read)."""
        try:
            return start_lines(flow_from_data(self.data, self.path or Path("flow.yaml")), registry)
        except FlowError:
            return {}

    # --- editing -----------------------------------------------------------------

    # --- text on the canvas ---------------------------------------------------------------

    @property
    def notes(self) -> dict:
        """Text on the canvas: note id -> {text, x, y, and optionally style, bold, italic, underline, font}."""
        return self.data.get("notes") or {}

    def new_note_id(self) -> str:
        n = 1
        while f"note_{n}" in self.notes:
            n += 1
        return f"note_{n}"

    def add_note(self, x: float, y: float, text: str = "Text") -> str:
        self._change()
        notes = self.data["notes"] = self.data.get("notes") or {}  # (`notes:` with nothing after it is None)
        note_id = self.new_note_id()
        notes[note_id] = {"text": text, "x": round(x), "y": round(y)}
        self._done()
        return note_id

    def set_note_value(self, note_id: str, key: str, value):
        """None, "" or False removes the key (its default applies: normal style, the usual font)."""
        note = self.notes[note_id]
        if note.get(key) == value or (value in (None, "", False) and key not in note):
            return
        self._change()
        if value in (None, "", False):
            note.pop(key, None)
        else:
            note[key] = value
        self._done()

    def set_note_values(self, note_id: str, values: dict):
        """Set several values of a text in one step (None, "" or False removes one)."""
        note = self.notes[note_id]
        if all(note.get(k) == v or (v in (None, "", False) and k not in note) for k, v in values.items()):
            return
        self._change()
        for key, value in values.items():
            if value in (None, "", False):
                note.pop(key, None)
            else:
                note[key] = value
        self._done()

    def move_notes(self, positions: dict):
        self._change()
        for note_id, (x, y) in positions.items():
            self.notes[note_id].update(x=round(x), y=round(y))
        self._done()

    def new_block_id(self, type_id: str) -> str:
        base = re.sub(r"\W", "_", type_id.split(".")[-1]) or "block"
        if not IDENTIFIER.match(base):
            base = f"b_{base}"
        candidate, n = base, 2
        while candidate in self.blocks:
            candidate, n = f"{base}_{n}", n + 1
        return candidate

    def add_block(self, type_id: str, x: float, y: float, config: dict | None = None) -> str:
        self._change()
        block_id = self.new_block_id(type_id)
        self.blocks[block_id] = {"type": type_id, "config": dict(config or {}), "ui": {"x": round(x), "y": round(y)}}
        self._done()
        return block_id

    def paste_blocks(self, blocks: dict, edges: list[str], notes: dict, dx: float,
                     dy: float) -> tuple[list[str], list[str]]:
        """Add copies of blocks (new ids) and the edges between them, and of texts (new ids), all moved by dx, dy,
        in one step. Returns the new block ids and note ids."""
        self._change()
        renamed = {}
        for old_id, spec in blocks.items():
            spec = copy.deepcopy(spec)
            new_id = self.new_block_id(old_id)
            ui = spec.setdefault("ui", {})
            ui["x"], ui["y"] = round(ui.get("x", 0) + dx), round(ui.get("y", 0) + dy)
            self.blocks[new_id] = spec
            renamed[old_id] = new_id
        for text in edges:
            e = parse_edge(text)
            if e and e[0] in renamed and e[2] in renamed:
                self.data["edges"].append(edge_text(renamed[e[0]], e[1], renamed[e[2]], e[3]))
        if notes:
            self.data["notes"] = self.notes  # (a flow without texts has no `notes:`)
        new_notes = []
        for note in notes.values():
            note = copy.deepcopy(note)
            note["x"], note["y"] = round(note.get("x", 0) + dx), round(note.get("y", 0) + dy)
            note_id = self.new_note_id()
            self.data["notes"][note_id] = note
            new_notes.append(note_id)
        self._done()
        return list(renamed.values()), new_notes

    def remove(self, block_ids=(), edges=(), notes=()):
        self._change()
        for block_id in block_ids:
            self.blocks.pop(block_id, None)
        for note_id in notes:
            self.notes.pop(note_id, None)
        if "notes" in self.data and not self.data["notes"]:
            del self.data["notes"]
        gone = set(block_ids)
        drop = {parse_edge(t) for t in edges}  # compare parsed, so spacing in the file doesn't matter
        self.data["edges"] = [t for t in self.data["edges"]
                              if not ((e := parse_edge(t)) and (e in drop or e[0] in gone or e[2] in gone))]
        self._done()

    def connect(self, src, src_port, dst, dst_port, many: bool = False):
        """Connect an output to an input. Most inputs take one edge, so an older one is replaced;
        a `many` input (e.g. email attachments) keeps them all."""
        if src == dst or (src, src_port, dst, dst_port) in self.edges():
            return
        self._change()
        if not many:
            self.data["edges"] = [t for t in self.data["edges"]
                                  if not ((e := parse_edge(t)) and e[2] == dst and e[3] == dst_port)]
        self.data["edges"].append(edge_text(src, src_port, dst, dst_port))
        self._done()

    def move_blocks(self, positions: dict):
        self._change()
        for block_id, (x, y) in positions.items():
            self.blocks[block_id].setdefault("ui", {}).update(x=round(x), y=round(y))
        self._done()

    def rename_block(self, old: str, new: str):
        if new == old:
            return
        if not IDENTIFIER.match(new) or new == "error":
            raise ValueError(f"'{new}' is not a valid block name (letters, digits and _)")
        if new in self.blocks:
            raise ValueError(f"a block named '{new}' already exists")
        self._change()
        self.renamed[old] = new  # (the canvas keeps it selected under its new name)
        self.data["blocks"] = {(new if k == old else k): v for k, v in self.blocks.items()}
        edges = []
        for text in self.data["edges"]:
            e = parse_edge(text)
            if e:
                src, sp, dst, dp = e
                text = edge_text(new if src == old else src, sp, new if dst == old else dst, dp)
            edges.append(text)
        self.data["edges"] = edges
        self._done()

    def set_block_value(self, block_id: str, key: str, value, section: str | None = "config"):
        """Set config[key] (section="config"), or a top-level block key like retry/timeout (section=None).

        None or "" removes the key so the block's default applies.
        """
        spec = self.blocks[block_id]
        target = spec.setdefault(section, {}) if section else spec
        if target.get(key) == value or (value in (None, "") and key not in target):
            return
        self._change()
        if value in (None, ""):
            target.pop(key, None)
        else:
            target[key] = value
        self._done()

    def set_port_names(self, block_id: str, key: str, value, old: list, new: list, outputs: bool):
        """Set a list of a block's input or output names (e.g. a Python Code block's outputs) and
        update their connections: a renamed one (another name in the same place) keeps them, a
        removed one loses them. `old` and `new` are the names in effect before and after."""
        renamed = ({o: n for o, n in zip(old, new) if o != n and o not in new and n not in old}
                   if len(old) == len(new) else {})
        removed = set(old) - set(new) - set(renamed)
        self._change()
        spec = self.blocks[block_id].setdefault("config", {})
        if value in (None, ""):
            spec.pop(key, None)
        else:
            spec[key] = value
        edges = []
        for text in self.data["edges"]:
            e = parse_edge(text)
            if e:
                src, sp, dst, dp = e
                block, port = (src, sp) if outputs else (dst, dp)
                if block == block_id and port in removed:
                    continue
                if block == block_id and port in renamed:
                    sp, dp = (renamed[sp], dp) if outputs else (sp, renamed[dp])
                    text = edge_text(src, sp, dst, dp)
            edges.append(text)
        self.data["edges"] = edges
        self._done()

    def add_manual_start(self, registry) -> str:
        """Add a Manual Start, connected to the flow's first blocks (it then runs as before: every block)."""
        from ..upgrade import add_manual_start

        self._change()
        name = add_manual_start(self.data, registry)
        self._done()
        return name

    def set_block_values(self, block_id: str, values: dict):
        """Set several config values of a block in one step (None or "" removes one)."""
        config = self.blocks[block_id].setdefault("config", {})
        if all(config.get(k) == v or (v in (None, "") and k not in config) for k, v in values.items()):
            return
        self._change()
        for key, value in values.items():
            if value in (None, ""):
                config.pop(key, None)
            else:
                config[key] = value
        self._done()

    def set_flow_value(self, key: str, value):
        if self.data.get(key) == value or (value in (None, "") and key not in self.data):
            return
        self._change()
        if value in (None, ""):
            self.data.pop(key, None)
        else:
            self.data[key] = value
        self._done()

    def restore(self, data: dict):
        """Put back recovered unsaved changes (they stay unsaved until you save)."""
        self._change()
        self.data = data
        self._fill_in_lists()
        self._done()

    def set_params(self, params: dict):
        if params == self.data["params"]:
            return
        self._change()
        self.data["params"] = params
        self._done()
