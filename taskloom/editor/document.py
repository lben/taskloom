"""The flow being edited: the YAML data, saving, and undo/redo."""

from __future__ import annotations

import copy
import re
from pathlib import Path

import yaml
from PySide6.QtCore import QObject, Signal

from ..block import IDENTIFIER
from ..engine import lines as start_lines
from ..flow import (_EDGE, ONE_AFTER_ANOTHER, FlowError, flow_from_data, keep_source_names, lines_run, per_line,
                    read_flow_file, splitter_lines)
from ..registry import LOCAL, for_flow

UNDO_LIMIT = 200
# What a Local block's copies run (its Python Code settings): two definitions with these the same have the same code.
SAME_CODE = ("inputs", "outputs", "use_environment", "code")


DEFAULT_NAME = "untitled"  # a new flow's, until it is saved as a file


def new_flow_data(name: str = DEFAULT_NAME) -> dict:
    return {"taskloom": 1, "name": name, "params": {}, "blocks": {}, "edges": []}


def flow_text(data: dict) -> str:
    """The text of the flow file holding `data`, as File → Save writes it."""
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def parse_edge(text: str) -> tuple[str, str, str, str] | None:
    match = _EDGE.match(text)
    return match.groups() if match else None


def edge_text(src, src_port, dst, dst_port) -> str:
    return f"{src}.{src_port} -> {dst}.{dst_port}"


def _port_changes(old: list, new: list) -> tuple[dict, set]:
    """How a list of port names changed: {old name: new name} of those renamed (another name in the same place), and
    the names removed."""
    renamed = ({o: n for o, n in zip(old, new) if o != n and o not in new and n not in old}
               if len(old) == len(new) else {})
    return renamed, set(old) - set(new) - set(renamed)


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
        # The blocks folder's block types (registry.discover), once a tab shows the flow: then `registry` holds them and
        # the flow's own Local blocks, and connecting, removing, renaming and pasting also keep the names a block gives
        # what is connected into it (fields.SourceNames) in step, in the same step.
        self.block_types = None
        self._registry = None  # (the local_blocks and block types it was made from, and the registry: see registry)
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
        path.write_text(flow_text(data), encoding="utf-8", newline="\n")
        self.data, self.path = data, path  # only once written: a file that cannot be written leaves the flow as it was
        self.dirty = False
        self.changed.emit()

    @property
    def registry(self):
        """The block types of the flow as it is now (None until a tab shows it): the blocks folder's and the flow's own
        Local blocks (registry.for_flow), made again after any change to either (an edit, undo, a version restored)."""
        if self.block_types is None:
            return None
        local, known = self.data.get("local_blocks"), self.block_types.blocks
        if self._registry is None or self._registry[:2] != (local, known):
            self._registry = copy.deepcopy(local), dict(known), for_flow(self.block_types, local)
        return self._registry[2]

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

    def _keep_names(self, moved: dict | None = None, within: set | None = None):
        """Also called before an edit that changes edges, block or output names or parameters: an output with no name
        stored (a file written by hand) then keeps the one it had, as a default depends on all of them."""
        if self.registry is not None:
            keep_source_names(self.data, self.registry, moved, within)

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

    def containers(self, block_id: str) -> list[str]:
        """The Loops (and Sessions) a block is inside, the innermost first (as flow.containers; they end at a name that
        is no block)."""
        found, loop = [], (self.blocks.get(block_id) or {}).get("inside")
        while isinstance(loop, str) and loop in self.blocks and loop not in found:
            found.append(loop)
            loop = self.blocks[loop].get("inside")
        return found

    def within(self, loop: str) -> list[str]:
        """The blocks inside a Loop, also those inside a Loop inside it."""
        return [b for b in self.blocks if loop in self.containers(b)]

    # --- Local blocks: definitions in the flow file (local_blocks), each shared by its copies, blocks of type
    # local.<name> (registry.for_flow) ---------------------------------------------------------------------------

    def local_name(self, block_id: str) -> str | None:
        """The name of the Local block the block is a copy of (None: it is no copy, or the flow file has no definition
        of it)."""
        type_id, local = (self.blocks.get(block_id) or {}).get("type"), self.data.get("local_blocks")
        name = type_id[len(LOCAL):] if isinstance(type_id, str) and type_id.startswith(LOCAL) else None
        return name if isinstance(local, dict) and isinstance(local.get(name), dict) else None

    def copies(self, name: str) -> list[str]:
        """The copies of the Local block `name`."""
        return [b for b, spec in self.blocks.items() if spec.get("type") == f"{LOCAL}{name}"]

    def settings(self, block_id: str) -> dict:
        """The block's settings as the flow file holds them: its config, or a copy's Local block's definition, which
        all its copies share (an edit there changes every copy)."""
        name = self.local_name(block_id)
        return self.data["local_blocks"][name] if name is not None else self.blocks[block_id].get("config") or {}

    def _settings_to_edit(self, block_id: str) -> dict:
        """settings, its config put in place when it has none."""
        name = self.local_name(block_id)
        return self.data["local_blocks"][name] if name is not None else self.blocks[block_id].setdefault("config", {})

    def _definitions(self) -> dict:
        """The flow file's local_blocks, put in place (before its blocks) when it has none."""
        if not isinstance(self.data.get("local_blocks"), dict):
            items = [(k, v) for k, v in self.data.items() if k != "local_blocks"]
            at = [k for k, _ in items].index("blocks")
            self.data = dict(items[:at] + [("local_blocks", {})] + items[at:])
        return self.data["local_blocks"]

    def _drop_unused_definitions(self):
        """Only the definitions of Local blocks that have a copy stay in the flow file."""
        local = self.data.get("local_blocks")
        if isinstance(local, dict):
            for name in [n for n in local if not self.copies(n)]:
                del local[name]
            if not local:
                del self.data["local_blocks"]

    def make_local(self, block_id: str, name: str, definition: dict):
        """Keep `definition` in the flow file as its Local block `name`, the block its first copy (of type local.<name>,
        with no config of its own; its name, place, lines and typed values stay), in one step."""
        self._change()
        self._keep_names()
        self._definitions()[name] = definition
        spec = self.blocks[block_id]
        spec["type"] = f"{LOCAL}{name}"
        spec.pop("config", None)
        spec.pop("version", None)
        self._done()

    def promote(self, name: str, type_id: str):
        """Every copy of the Local block `name` becomes a block of type `type_id` (the global block made from it), and
        its definition leaves the flow file, in one step."""
        self._change()
        for block_id in self.copies(name):
            self.blocks[block_id]["type"] = type_id
        self._drop_unused_definitions()
        self._done()

    def _add_definitions(self, blocks: dict, local_blocks: dict) -> dict[str, str]:
        """Pasting `blocks`: the definitions (`local_blocks`, as copied) of the Local blocks they are copies of. One the
        flow has under that name with the same code (SAME_CODE; its title and help may differ) joins it, the flow's
        definition kept as it is; one it has with other code, or whose name or title a Global block has (block_types),
        is kept under the first free name <name>_2, <name>_3, ..., titled so too. Returns {name: the name it is kept
        as} of those renamed."""
        names = {spec["type"][len(LOCAL):] for spec in blocks.values()
                 if isinstance(spec, dict) and str(spec.get("type")).startswith(LOCAL)}
        known = self.block_types.blocks if self.block_types is not None else {}
        renamed = {}
        for name in sorted(n for n in names if isinstance(local_blocks.get(n), dict)):
            mine = self.data.get("local_blocks") if isinstance(self.data.get("local_blocks"), dict) else {}
            n, new, wanted = 1, name, copy.deepcopy(local_blocks[name])
            while not (isinstance(mine.get(new), dict) and all(mine[new].get(k) == wanted.get(k) for k in SAME_CODE)):
                if new not in mine and f"user.{new}" not in known and wanted.get("title") not in {
                        cls.title for cls in known.values() if cls.title}:
                    self._definitions()[new] = wanted
                    break
                n += 1
                new = f"{name}_{n}"
                wanted = {**copy.deepcopy(local_blocks[name]), "title": f"{local_blocks[name].get('title') or name} {n}"}
            if new != name:
                renamed[name] = new
        return renamed

    def lines(self, registry) -> dict[str, set[str]]:
        """Each start block's line as the flow is now: the blocks its runs take part in ({} while it cannot be read)."""
        try:
            return start_lines(flow_from_data(self.data, self.path or Path("flow.yaml")), registry)
        except FlowError:
            return {}

    def numbered_lines(self, registry) -> dict[str, dict[str, int]]:
        """The Splitters that run their lines one after another, with the number of each of their lines by its first
        block, in line order ({} while the flow cannot be read)."""
        try:
            flow = flow_from_data(self.data, self.path or Path("flow.yaml"))
        except FlowError:
            return {}
        return {splitter: {first: number for number, (first, _) in enumerate(its_lines, 1)}
                for splitter, its_lines in splitter_lines(flow).items()
                if lines_run(flow, registry, splitter)[0] == ONE_AFTER_ANOTHER}

    def per_line(self, registry) -> dict[str, set[str]]:
        """Each Merger set to each line as it finishes, with the blocks it runs once per line (flow.per_line), as the
        flow is now ({} while it cannot be read)."""
        try:
            return per_line(flow_from_data(self.data, self.path or Path("flow.yaml")), registry)
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

    def add_block(self, type_id: str, x: float, y: float, config: dict | None = None, inside: str | None = None) -> str:
        """Add a block (`inside`: the Loop it is added in)."""
        self._change()
        block_id = self.new_block_id(type_id)
        self.blocks[block_id] = {"type": type_id, "config": dict(config or {}), "ui": {"x": round(x), "y": round(y)},
                                 **({"inside": inside} if inside else {})}
        self._done()
        return block_id

    def paste_blocks(self, blocks: dict, edges: list[str], notes: dict, x: float, y: float,
                     local_blocks: dict | None = None) -> tuple[list[str], list[str], dict[str, str]]:
        """Add copies of blocks (new ids) and the edges between them, and of texts (new ids), in one step, in their
        layout, with the group's top-left corner (its smallest x and y) at x, y; a copy is inside the copy of the Loop
        it was inside (copied with it), or in none. Copies of Local blocks come with their definitions (`local_blocks`,
        as copied: see _add_definitions). Returns the new block ids and note ids, and {name: the name it is kept as} of
        the Local blocks pasted under another name."""
        spots = [(spec.get("ui") or {}) for spec in blocks.values()] + list(notes.values())
        dx, dy = x - min(s.get("x", 0) for s in spots), y - min(s.get("y", 0) for s in spots)
        self._change()
        kept_as = self._add_definitions(blocks, local_blocks or {})
        renamed = {}
        for old_id, spec in blocks.items():
            spec = copy.deepcopy(spec)
            if str(spec.get("type")).startswith(LOCAL) and spec["type"][len(LOCAL):] in kept_as:
                spec["type"] = f"{LOCAL}{kept_as[spec['type'][len(LOCAL):]]}"
            new_id = self.new_block_id(old_id)
            ui = spec.setdefault("ui", {})
            ui["x"], ui["y"] = round(ui.get("x", 0) + dx), round(ui.get("y", 0) + dy)
            self.blocks[new_id] = spec
            renamed[old_id] = new_id
        for new_id in renamed.values():
            spec = self.blocks[new_id]
            if "inside" in spec:
                if spec["inside"] in renamed:
                    spec["inside"] = renamed[spec["inside"]]
                else:
                    del spec["inside"]
        moved = {}  # the copies' names of what is connected into them follow the copied blocks
        for text in edges:
            e = parse_edge(text)
            if e and e[0] in renamed and e[2] in renamed:
                self.data["edges"].append(edge_text(renamed[e[0]], e[1], renamed[e[2]], e[3]))
                moved[f"{e[0]}.{e[1]}"] = f"{renamed[e[0]]}.{e[1]}"
        self._keep_names(moved, set(renamed.values()))
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
        return list(renamed.values()), new_notes, kept_as

    def remove(self, block_ids=(), edges=(), notes=()):
        """Remove blocks (with their connections), connections and texts, in one step. The blocks inside a Loop
        removed stay, inside the Loop it was inside, or in none; a Local block's definition goes with its last copy."""
        self._change()
        self._keep_names()
        gone = set(block_ids)
        for block_id, spec in self.blocks.items():
            if block_id not in gone and spec.get("inside") in gone:
                left = next((c for c in self.containers(block_id) if c not in gone), None)
                if left is None:
                    del spec["inside"]
                else:
                    spec["inside"] = left
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
        self._drop_unused_definitions()  # (the last copy of a Local block removed: its definition with it)
        self._keep_names()
        self._done()

    def connect(self, src, src_port, dst, dst_port, many: bool = False):
        """Connect an output to an input. Most inputs take one edge, so an older one is replaced;
        a `many` input (e.g. email attachments) keeps them all. The line replaces the input's typed value, in the same
        step."""
        if src == dst or (src, src_port, dst, dst_port) in self.edges():
            return
        self._change()
        if not many:
            self.data["edges"] = [t for t in self.data["edges"]
                                  if not ((e := parse_edge(t)) and e[2] == dst and e[3] == dst_port)]
        self.data["edges"].append(edge_text(src, src_port, dst, dst_port))
        typed = (self.blocks.get(dst) or {}).get("inputs") or {}
        if dst_port in typed:
            self._set_typed_values(dst, {hole: t for hole, t in typed.items() if hole != dst_port})
        self._keep_names()
        self._done()

    def _set_typed_values(self, block_id: str, typed: dict):
        """A block's typed values ({input: {type, value}}): none leaves no `inputs:` behind."""
        if typed:
            self.blocks[block_id]["inputs"] = typed
        else:
            self.blocks[block_id].pop("inputs", None)

    def move_blocks(self, positions: dict, inside: dict | None = None):
        """Move blocks ({block: (x, y)}) and, in the same step, put some inside a Loop or in none ({block: the Loop,
        or None})."""
        self._change()
        for block_id, (x, y) in positions.items():
            self.blocks[block_id].setdefault("ui", {}).update(x=round(x), y=round(y))
        for block_id, loop in (inside or {}).items():
            if loop:
                self.blocks[block_id]["inside"] = loop
            else:
                self.blocks[block_id].pop("inside", None)
        self._done()

    def set_box(self, block_id: str, width: float, height: float):
        """A Loop's box resized: its size from its top-left (as the flow file has it, ui x and y)."""
        ui = self.blocks[block_id].setdefault("ui", {})
        size = {"width": max(round(width), 1), "height": max(round(height), 1)}
        if all(ui.get(k) == v for k, v in size.items()):
            return
        self._change()
        ui.update(size)
        self._done()

    def rename_block(self, old: str, new: str):
        if new == old:
            return
        if not IDENTIFIER.match(new) or new == "error":
            raise ValueError(f"'{new}' is not a valid block name (letters, digits and _)")
        if new in self.blocks:
            raise ValueError(f"a block named '{new}' already exists")
        self._change()
        self._keep_names()
        moved = {f"{old}.{port}": f"{new}.{port}" for src, port, _, _ in self.edges() if src == old}
        self.renamed[old] = new  # (the canvas keeps it selected under its new name)
        self.data["blocks"] = {(new if k == old else k): v for k, v in self.blocks.items()}
        for spec in self.blocks.values():  # (a Loop: the blocks inside it are inside it under its new name)
            if spec.get("inside") == old:
                spec["inside"] = new
        edges = []
        for text in self.data["edges"]:
            e = parse_edge(text)
            if e:
                src, sp, dst, dp = e
                text = edge_text(new if src == old else src, sp, new if dst == old else dst, dp)
            edges.append(text)
        self.data["edges"] = edges
        self._keep_names(moved)
        self._done()

    def set_block_value(self, block_id: str, key: str, value, section: str | None = "config"):
        """Set config[key] (section="config"; a copy's: in its Local block's definition, see settings), the typed value
        of the input `key` (section="inputs": {type, value}, see flow.BLOCK_KEYS; the canvas and Properties both set it
        here), or a top-level block key like retry/timeout (section=None).

        None or "" removes the key so the block's default applies (an input: it has no value).
        """
        spec = self.blocks[block_id]
        if section == "inputs":
            target = spec.get("inputs") or {}  # (put in place by _set_typed_values, only if a value is left)
        elif section == "config":
            target = self._settings_to_edit(block_id)
        else:
            target = spec.setdefault(section, {}) if section else spec
        if target.get(key) == value or (value in (None, "") and key not in target):
            return
        self._change()
        if value in (None, ""):
            target.pop(key, None)
        else:
            target[key] = value
        if section == "inputs":
            self._set_typed_values(block_id, target)
        self._done()

    def set_port_names(self, block_id: str, key: str, value, old: list, new: list, outputs: bool):
        """Set a list of a block's input or output names (e.g. a Python Code block's outputs) and
        update their connections, and an input's typed value: a renamed one (another name in the same place) keeps
        them, a removed one loses them. `old` and `new` are the names in effect before and after. A copy's are its
        Local block's (settings): every copy's follow, and removing an input a copy has a line or a typed value on is
        refused (ValueError naming each such copy and what it has there), nothing changed."""
        renamed, removed = _port_changes(old, new)
        name = self.local_name(block_id)
        blocks = self.copies(name) if name is not None else [block_id]
        if name is not None and not outputs:
            self._refuse_removing(blocks, [hole for hole in old if hole in removed])
        self._change()
        self._keep_names()
        spec = self._settings_to_edit(block_id)
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
                if block in blocks and port in removed:
                    continue
                if block in blocks and port in renamed:
                    sp, dp = (renamed[sp], dp) if outputs else (sp, renamed[dp])
                    text = edge_text(src, sp, dst, dp)
            edges.append(text)
        self.data["edges"] = edges
        for block in blocks if not outputs else ():
            typed = self.blocks[block].get("inputs") or {}
            self._set_typed_values(block, {renamed.get(hole, hole): t for hole, t in typed.items()
                                           if hole not in removed})
        self._keep_names({f"{block}.{o}": f"{block}.{n}" for block in blocks for o, n in renamed.items()}
                         if outputs else None)
        self._done()

    def _refuse_removing(self, copies: list[str], holes: list[str]):
        """Raises ValueError when one of the `copies` has a line or a typed value on one of its input `holes`, saying
        which and what it has."""
        why = []
        for hole in holes:
            has = [f"{dst} has a line from {src}.{sp}" for src, sp, dst, dp in self.edges()
                   if dst in copies and dp == hole]
            has += [f"{block} has a typed value" for block in copies if hole in (self.blocks[block].get("inputs") or {})]
            if has:
                why.append(f"Cannot remove the input {hole}: {'; '.join(has)}.")
        if why:
            raise ValueError(" ".join(why) + " Delete those lines and clear those values (right-click the input → "
                                             "Clear value) first: every copy of a Local block has its inputs.")

    def add_manual_start(self, registry) -> str:
        """Add a Manual Start, connected to the flow's first blocks (it then runs as before: every block)."""
        from ..upgrade import add_manual_start

        self._change()
        name = add_manual_start(self.data, registry)
        self._done()
        return name

    def set_block_values(self, block_id: str, values: dict, top: dict | None = None):
        """Set several config values of a block in one step (None or "" removes one), and in the same step the
        top-level block keys in `top` (e.g. timeout)."""
        spec = self.blocks[block_id]
        config = spec.setdefault("config", {})
        changes = [(config, k, v) for k, v in values.items()] + [(spec, k, v) for k, v in (top or {}).items()]
        if all(target.get(k) == v or (v in (None, "") and k not in target) for target, k, v in changes):
            return
        self._change()
        for target, key, value in changes:
            if value in (None, ""):
                target.pop(key, None)
            else:
                target[key] = value
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

    def restore(self, data: dict) -> dict:
        """Put back recovered unsaved changes, or a version from the Git history (unsaved until you save), in one
        step. Returns that step's undo (see in_effect)."""
        self._change()
        undo = self._undo[-1]
        self.data = data
        self._fill_in_lists()
        self._done()
        return undo

    def in_effect(self, undo: dict) -> bool:
        """Whether the step that restore returned `undo` for is in the flow (as far back as Undo reaches): not undone,
        or redone since."""
        return any(step is undo for step in self._undo)

    def set_params(self, params: dict):
        if params == self.data["params"]:
            return
        self._change()
        self._keep_names()
        self.data["params"] = params
        self._done()
