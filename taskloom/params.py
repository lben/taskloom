"""Typed flow parameters, text templates and SQL bind parameters."""

from __future__ import annotations

import datetime as dt
import re
import string

from .expr import evaluate

TYPES = ("text", "int", "float", "bool", "date", "datetime", "list")


def coerce(type_: str, value):
    """Convert a value (or a command-line string) to a parameter type."""
    if type_ == "text":
        return str(value)
    if type_ == "int":
        if isinstance(value, bool) or isinstance(value, float) and not value.is_integer():
            raise ValueError(f"expected an integer, got {value!r}")
        return int(value)
    if type_ == "float":
        return float(value)
    if type_ == "bool":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ("true", "yes", "1"):
            return True
        if text in ("false", "no", "0"):
            return False
        raise ValueError(f"expected true/false, got {value!r}")
    if type_ == "date":
        if isinstance(value, dt.datetime):
            return value.date()
        if isinstance(value, dt.date):
            return value
        return dt.date.fromisoformat(str(value))
    if type_ == "datetime":
        if isinstance(value, dt.datetime):
            return value
        if isinstance(value, dt.date):
            return dt.datetime.combine(value, dt.time())
        return dt.datetime.fromisoformat(str(value))
    if type_ == "list":
        if isinstance(value, (list, tuple)):
            return list(value)
        return [item.strip() for item in str(value).split(",") if item.strip()]
    raise ValueError(f"unknown parameter type '{type_}' (expected one of {', '.join(TYPES)})")


def resolve(specs: dict, overrides: dict, funcs: dict) -> dict:
    """Compute parameter values in declaration order.

    An overridden parameter takes the given value; later expressions see it, so
    `day_key` follows an overridden `day`.
    """
    unknown = set(overrides) - set(specs)
    if unknown:
        raise ValueError(f"unknown parameter(s): {', '.join(sorted(unknown))}")
    values: dict = {}
    for name, spec in specs.items():
        try:
            if name in overrides:
                raw = overrides[name]
            elif "expr" in spec:
                raw = evaluate(spec["expr"], {**funcs, **values})
            else:
                raw = spec.get("value")
            values[name] = coerce(spec["type"], raw)
        except Exception as e:
            raise ValueError(f"parameter '{name}': {e}") from None
    return values


class _Formatter(string.Formatter):
    def get_field(self, field_name, args, kwargs):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", field_name):
            raise ValueError(f"'{{{field_name}}}' is not a parameter name")
        if field_name not in kwargs:
            raise KeyError(field_name)
        return kwargs[field_name], field_name


def render(template: str, values: dict) -> str:
    """Fill `{name}` / `{name:format}`. Write `{{` and `}}` for literal braces."""
    try:
        return _Formatter().vformat(template, (), values)
    except KeyError as e:
        raise ValueError(f"unknown parameter {{{e.args[0]}}} (write {{{{ and }}}} for literal braces)") from None
    except (ValueError, IndexError) as e:
        raise ValueError(f"invalid template {template!r}: {e}") from None


def template_names(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


_SQL_TOKENS = re.compile(
    r"""'(?:[^']|'')*'        # string literal
      | "(?:[^"]|"")*"        # quoted identifier
      | `[^`]*`               # backtick identifier
      | --[^\n]*              # line comment
      | /\*.*?\*/             # block comment
      | ::                    # cast operator
      | :([A-Za-z_][A-Za-z0-9_]*)  # bind parameter
    """,
    re.VERBOSE | re.DOTALL,
)


def to_qmark(sql: str) -> tuple[str, list[str]]:
    """Rewrite `:name` bind parameters as `?`; return the SQL and the names in order."""
    names: list[str] = []

    def replace(match):
        if match.group(1):
            names.append(match.group(1))
            return "?"
        return match.group(0)

    return _SQL_TOKENS.sub(replace, sql), names
