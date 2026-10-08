"""The block plugin API: base class, port types and config field types."""

from __future__ import annotations

import re
from types import SimpleNamespace

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ports:
    """Port types. A block declares `inputs` and `outputs` as {name: port}."""

    class Port:
        kind = "any"

        def __init__(self, required: bool = True):
            self.required = required

    class Any(Port):
        kind = "any"

    class Table(Port):
        kind = "table"


class fields:
    """Config field types. The editor builds the properties panel from these."""

    class Field:
        # Templated fields get `{param}` / `{param:format}` substitution at run time.
        template = False

        def __init__(self, default=None, required: bool | None = None, help: str = ""):
            self.default = default
            self.required = default is None if required is None else required
            self.help = help

        def coerce(self, value):
            return value

    class Text(Field):
        template = True

        def coerce(self, value):
            return str(value)

    class Int(Field):
        def coerce(self, value):
            if isinstance(value, bool) or not isinstance(value, (int, str)):
                raise ValueError(f"expected an integer, got {value!r}")
            return int(value)

    class Float(Field):
        def coerce(self, value):
            if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                raise ValueError(f"expected a number, got {value!r}")
            return float(value)

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
        pass

    class Connection(Field):
        def __init__(self, kind: str, default=None, **kw):
            super().__init__(default=default, **kw)
            self.kind = kind

        def coerce(self, value):
            return str(value)

    class Names(Field):
        """A list of identifiers, e.g. the input names of a code block."""

        def coerce(self, value):
            if not isinstance(value, list) or not all(isinstance(v, str) and IDENTIFIER.match(v) for v in value):
                raise ValueError(f"expected a list of names (letters, digits, _), got {value!r}")
            if len(set(value)) != len(value):
                raise ValueError(f"duplicate names in {value!r}")
            return list(value)


class Block:
    """Base class for all blocks. Subclass it, set the class attributes, implement run()."""

    type_id: str = ""  # stable id stored in flow files, e.g. "db.run_query"
    version: int = 1
    title: str = ""
    category: str = "Other"
    inputs: dict = {}
    outputs: dict = {}
    config: dict = {}

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
