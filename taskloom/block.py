"""The block plugin API: base class, port types and config field types."""

from __future__ import annotations

import re
from types import SimpleNamespace

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
AFTER = "after"  # input of every block: run after another block (its `done`) or a start block
DONE = "done"  # output of every block: fires when it succeeded, to connect to another block's `after`
RESERVED_INPUTS, RESERVED_OUTPUTS = {AFTER}, {DONE, "error"}


class ports:
    """Port types. A block declares `inputs` and `outputs` as {name: port}."""

    class Port:
        kind = "any"

        def __init__(self, required: bool = True, many: bool = False, any_of: bool = False):
            self.required = required
            # many: takes several edges and always receives a list, in edge order.
            self.many = many
            # any_of: takes several edges and runs when any one of them delivers (two paths that meet,
            # or several blocks' errors going to one handler). With several edges it receives a list.
            self.any_of = any_of

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
                raise ValueError(f"{', '.join(sorted(taken))}: every block already has these; choose other names")
            return list(value)


def setting_label(key: str, f: fields.Field) -> str:
    """The name the editor's Properties panel shows for the setting `key` (and messages about it use)."""
    words = key.replace("_", " ").capitalize().split(" ")
    return f.label or " ".join(w.upper() if w.lower() in ("url", "sql", "pid", "id") else w for w in words)


class Block:
    """Base class for all blocks. Subclass it, set the class attributes, implement run()."""

    type_id: str = ""  # stable id stored in flow files, e.g. "db.run_query"
    version: int = 1
    title: str = ""
    # What the block does, in plain words; shown when hovering over it in the editor.
    description: str = ""
    category: str = "Other"
    inputs: dict = {}
    outputs: dict = {}
    config: dict = {}

    # Start blocks (Manual Start, Schedule) begin a run; see taskloom/blocks/triggers.py.
    is_trigger: bool = False

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

    def run(self, ctx, **inputs) -> dict:
        raise NotImplementedError


def all_ports(cls, config: dict) -> tuple[dict, dict]:
    """A block's ports plus the `after` input and `done` output every block has (they only set the order)."""
    inputs, outputs = cls.ports(config)
    if not cls.is_trigger:
        if AFTER not in inputs:
            inputs = {**inputs, AFTER: ports.Any(required=False, any_of=True)}
        outputs = {DONE: ports.Any(), **outputs}
    return inputs, outputs
