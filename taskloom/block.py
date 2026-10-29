"""The block plugin API: base class, port types and config field types."""

from __future__ import annotations

import itertools
import re
from types import SimpleNamespace

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Input of every block but a start block: it waits for what is connected there (any output of another block, or a
# start block's `start`) and does not use its value.
AFTER = "after"
# Output of a block that may succeed without delivering its other outputs (see Block.has_done): fires when it
# succeeded, to connect to another block's `after`.
DONE = "done"
RESERVED_INPUTS, RESERVED_OUTPUTS = {AFTER}, {DONE, "error"}


class ports:
    """Port types. A block declares `inputs` and `outputs` as {name: port}."""

    class Port:
        kind = "any"

        def __init__(self, required: bool = True, many: bool = False, any_of: bool = False,
                     by_source: bool = False):
            self.required = required
            # many: takes several edges and always receives a list, in edge order.
            self.many = many
            # any_of: takes several edges and runs when any one of them delivers (two paths that meet,
            # or several blocks' errors going to one handler). With several edges it receives a list.
            self.any_of = any_of
            # by_source: with several edges it receives a dict instead, {source block: value} (see
            # engine._arriving). With many too, always a dict, keyed block.port, even with one edge.
            self.by_source = by_source

    class Any(Port):
        kind = "any"

    class Table(Port):
        kind = "table"


class fields:
    """Config field types. The editor builds the properties panel from these."""

    class Field:
        # Templated fields get `{param}` / `{param:format}` substitution at run time.
        template = False

        def __init__(self, default=None, required: bool | None = None, help: str = "", label: str = ""):
            self.default = default
            self.required = default is None if required is None else required
            self.help = help
            self.label = label  # the name the editor shows; empty: made from the config key

        def coerce(self, value):
            return value

    class Text(Field):
        template = True

        def coerce(self, value):
            return str(value)

    class Int(Field):
        def __init__(self, default=None, minimum: int | None = None, **kw):
            super().__init__(default=default, **kw)
            self.minimum = minimum

        def coerce(self, value):
            if isinstance(value, bool) or not isinstance(value, (int, str)):
                raise ValueError(f"expected an integer, got {value!r}")
            value = int(value)
            if self.minimum is not None and value < self.minimum:
                raise ValueError(f"expected {self.minimum} or more, got {value}")
            return value

    class Float(Field):
        def coerce(self, value):
            if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                raise ValueError(f"expected a number, got {value!r}")
            return float(value)

    class Bool(Field):
        def coerce(self, value):
            if not isinstance(value, bool):
                raise ValueError(f"expected true or false, got {value!r}")
            return value

    class Choice(Field):
        def __init__(self, options, default=None, **kw):
            super().__init__(default=default, **kw)
            self.options = list(options)

        def coerce(self, value):
            if value not in self.options:
                raise ValueError(f"expected one of {self.options}, got {value!r}")
            return value

    class Code(Field):
        def __init__(self, language: str, default=None, **kw):
            super().__init__(default=default, **kw)
            self.language = language
            self.template = language == "sql"

        def coerce(self, value):
            return str(value)

    class Path(Text):
        def __init__(self, default=None, files=(), **kw):
            super().__init__(default=default, **kw)
            # The kinds of file its browse button offers, e.g. ["Excel workbook (*.xlsx)"]; none: any file.
            self.files = list(files)

    class Connection(Field):
        def __init__(self, kind: str, default=None, **kw):
            super().__init__(default=default, **kw)
            self.kind = kind

        def coerce(self, value):
            return str(value)

    class Server(Connection):
        """Where a path is: on this computer (left empty) or on the server of an SSH connection."""

        def __init__(self, **kw):
            super().__init__("ssh", required=False, **kw)

    class List(Field):
        """A list of text values."""

        def coerce(self, value):
            if not isinstance(value, list):
                raise ValueError(f"expected a list, got {value!r}")
            return [str(v) for v in value]

    class Names(Field):
        """A list of identifiers, e.g. the input names of a code block (the names of its ports)."""

        def __init__(self, default=None, outputs: bool = False, **kw):
            super().__init__(default=default, **kw)
            self.reserved = RESERVED_OUTPUTS if outputs else RESERVED_INPUTS

        def coerce(self, value):
            if not isinstance(value, list) or not all(isinstance(v, str) and IDENTIFIER.match(v) for v in value):
                raise ValueError(f"expected a list of names (letters, digits, _), got {value!r}")
            if len(set(value)) != len(value):
                raise ValueError(f"duplicate names in {value!r}")
            taken = self.reserved & set(value)
            if taken:
                raise ValueError(f"{', '.join(sorted(taken))}: this block already has these; choose other names")
            return list(value)

    class SourceNames(Field):
        """A name for each output connected into the block's input `port`, {"block.port": name}, e.g. for a template
        to say where each one goes. An output with no name stored has the one in_effect gives it."""

        def __init__(self, port: str, **kw):
            super().__init__(default={}, **kw)
            self.port = port

        def coerce(self, value):
            if not isinstance(value, dict):
                raise ValueError(f"expected block.port: name for each output connected here, got {value!r}")
            for source, name in value.items():
                if not (isinstance(source, str) and len(parts := source.split(".")) == 2
                        and all(IDENTIFIER.fullmatch(p) for p in parts)):
                    raise ValueError(f"'{source}' should be block.port, the output it names, e.g. totals.result")
                if not (isinstance(name, str) and IDENTIFIER.fullmatch(name)):
                    raise ValueError(f"'{name}' is not a name (letters, digits and _, not starting with a digit)")
            return dict(value)

        @staticmethod
        def in_effect(names: dict, sources: list, params) -> dict:
            """The name of each source ("block.port", in edge order): its name in `names`, or else the first of its
            block's name, block_port, block_port_2, block_port_3, ... not taken by the names of the other sources in
            `names`, the names given to the sources before it, or a flow parameter (`params`: their names)."""
            taken = {str(names[s]) for s in sources if s in names} | set(params)  # (str: one written wrong by hand)
            result = {}
            for source in sources:
                if source in names:
                    result[source] = names[source]
                    continue
                block, port = source.split(".")
                candidates = itertools.chain([block, f"{block}_{port}"],
                                             (f"{block}_{port}_{n}" for n in itertools.count(2)))
                result[source] = next(c for c in candidates if c not in taken)
                taken.add(result[source])
            return result


def setting_label(key: str, f: fields.Field) -> str:
    """The name the editor's Properties panel shows for the setting `key` (and messages about it use)."""
    words = key.replace("_", " ").capitalize().split(" ")
    return f.label or " ".join(w.upper() if w.lower() in ("url", "sql", "pid", "id") else w for w in words)


class Block:
    """Base class for all blocks. Subclass it, set the class attributes, implement run().

    Every block but a start block has a `run after` input. A block whose run() may succeed without returning all its
    outputs (If, Python Code) sets has_done = True: it then also has a `done` output, which fires whenever it
    succeeds, for another block to run after it. A block without outputs has `done` anyway; the others do not need
    it, since an output that always arrives can go into another block's `run after`."""

    type_id: str = ""  # stable id stored in flow files, e.g. "db.run_query"
    version: int = 1
    title: str = ""
    # What the block does, in plain words; shown when hovering over it in the editor.
    description: str = ""
    inputs_help: str = ""  # what its inputs expect, in plain words; shown with the description
    category: str = "Other"
    inputs: dict = {}
    outputs: dict = {}
    config: dict = {}
    # The config a block added in the editor starts with (written into the flow file). A flow file's
    # block gets only what the file says, and the field defaults for the rest.
    new_config: dict = {}

    # Start blocks (Manual Start, Schedule) begin a run; see taskloom/blocks/triggers.py.
    is_trigger: bool = False
    has_done: bool = False  # a `done` output too (see above)

    def __init__(self, config: dict):
        self.config = SimpleNamespace(**config)

    @classmethod
    def ports(cls, config: dict) -> tuple[dict, dict]:
        """Inputs and outputs for a given config. Override for config-dependent ports."""
        return cls.inputs, cls.outputs

    @classmethod
    def migrate(cls, config: dict, old_version: int) -> dict:
        """Upgrade a config saved by an older version of this block."""
        return config

    @classmethod
    def problems(cls, config: dict, sources: dict, params: set) -> list[str]:
        """What stops the block from running as it is set up and connected, beyond a setting it cannot take: shown in
        Properties, and Validate and runs refuse the flow. `config`: its config as block_config gives it; `sources`:
        {input port: ["block.port", ...] connected there, in edge order}; `params`: the flow parameters' names."""
        return []

    def run(self, ctx, **inputs) -> dict:
        raise NotImplementedError


def has_done_output(cls, outputs: dict) -> bool:
    """Whether a block with these outputs (its ports for its config) has the `done` output: not a start block, and it
    sets has_done or has no outputs."""
    return not cls.is_trigger and (cls.has_done or not outputs)


def all_ports(cls, config: dict) -> tuple[dict, dict]:
    """A block's ports plus the `after` input every block but a start block has, and the `done` output of a block that
    has it (has_done_output), first; these two only set the order."""
    inputs, outputs = cls.ports(config)
    if not cls.is_trigger and AFTER not in inputs:
        inputs = {**inputs, AFTER: ports.Any(required=False, any_of=True)}
    if has_done_output(cls, outputs):
        outputs = {DONE: ports.Any(), **outputs}
    return inputs, outputs
