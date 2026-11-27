"""Typed flow parameters, text templates and SQL bind parameters."""

from __future__ import annotations

import datetime as dt
import decimal
import re

from .expr import evaluate

TYPES = ("text", "int", "float", "bool", "date", "datetime", "list")


def coerce(type_: str, value):
    """Convert a value (or a command-line string) to a parameter type."""
    if value is None:  # left empty (`value:` in the flow file): as the editor shows and previews it
        value = ""
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


# The one brace rule of every text that is filled: {{name}} or {{name:format}} (spaces inside, around the colon too,
# do not matter) is a placeholder, {{{{ a literal {{; a single brace, a lone }} and a backslash are plain text. A
# placeholder is on one line.
_BRACES = re.compile(r"\{\{\{\{|\{\{(?!\{)(?:(.*?)\}\})?")
LITERAL_BRACES = "write {{{{ for a literal {{"
# {{secret:CARD.key}} is always a reference to a credential (so no flow parameter or input is named secret).
SECRET = "secret"


def split(text: str) -> list[tuple[str, str | None]]:
    """`text` cut at its placeholders: [(plain text, what is inside the next {{ }}, stripped), ..., (plain text, None)].
    Raises ValueError for a {{ with no }} after it on its line, or an empty {{}}."""
    parts, plain, start = [], "", 0
    for m in _BRACES.finditer(text):
        plain += text[start:m.start()]
        start = m.end()
        if m.group() == "{{{{":
            plain += "{{"
        elif m.group(1) is None:
            raise ValueError(f"{{{{ with no }}}} after it ({LITERAL_BRACES})")
        elif not m.group(1).strip():
            raise ValueError(f"{m.group()} names nothing ({LITERAL_BRACES})")
        else:
            parts.append((plain, m.group(1).strip()))
            plain = ""
    return parts + [(plain + text[start:], None)]


def reference(inside: str | None) -> str | None:
    """The CARD.key of a placeholder that is a reference to a credential, {{secret:CARD.key}} (`inside`: what split
    found inside its braces); None for any other."""
    name, _, card_key = (inside or "").partition(":")
    return card_key.strip() if inside is not None and name.strip() == SECRET else None


def references(text: str) -> list[str]:
    """The CARD.key of each {{secret:CARD.key}} in `text`, in order. Raises ValueError as split does."""
    return [found for _, inside in split(text) if (found := reference(inside)) is not None]


def parse(text: str) -> list[tuple[str, str | None, str]]:
    """`text` cut at its placeholders {{name}} / {{name:format}}: [(plain text, name, format), ..., (plain text, None,
    "")]; a reference {{secret:CARD.key}} has the name SECRET and its CARD.key as the format. Raises ValueError as
    split does, or for a placeholder that is not a name."""
    parts = []
    for plain, inside in split(text):
        name, _, spec = (inside or "").partition(":")
        if inside is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name.strip()):
            raise ValueError(f"{{{{{inside}}}}} is not a name ({LITERAL_BRACES})")
        parts.append((plain, name.strip() if inside is not None else None, spec.strip()))
    return parts


def render(template: str, values: dict, secret) -> str:
    """`template` with each {{name}} / {{name:format}} filled in once from `values`, and each {{secret:CARD.key}} with
    secret(CARD.key): what is filled in is never filled again."""
    filled = []
    for plain, name, spec in parse(template):
        filled.append(plain)
        if name == SECRET:
            filled.append(secret(spec))
        elif name is not None:
            if name not in values:
                raise ValueError(f"unknown parameter {{{{{name}}}}}")
            filled.append(format(values[name], spec))
    return "".join(filled)


def template_names(template: str) -> set[str]:
    """The names of the placeholders of `template`, but references {{secret:CARD.key}}."""
    return {name for _, name, _ in parse(template) if name not in (None, SECRET)}


def single_braced(text: str) -> list[str]:
    """The names of the single-brace {name} and {name:format} of `text` outside its placeholders, each once: plain text,
    though probably meant as {{name}}."""
    found = re.findall(r"(?<!\$)\{([A-Za-z_]\w*)(?::[^{}\n]*)?\}", _BRACES.sub("\0", text))
    return list(dict.fromkeys(found))


_SQL_TOKENS = re.compile(
    r"""'(?:[^']|'')*'        # string literal
      | "(?:[^"]|"")*"        # quoted identifier
      | `[^`]*`               # backtick identifier
      | --[^\n]*              # line comment
      | /\*.*?\*/             # block comment
      | ::                    # cast operator
      | :([A-Za-z_][A-Za-z0-9_]*)  # bind parameter
      | (\?)                  # a value bound to it (in the SQL to_qmark gives)
    """,
    re.VERBOSE | re.DOTALL,
)


def without_comments(sql: str) -> str:
    """The SQL without its -- and /* */ comments (a -- or /* in quotes starts none)."""
    return _SQL_TOKENS.sub(lambda m: "" if m.group(0).startswith(("--", "/*")) else m.group(0), sql)


def to_qmark(sql: str, values: dict | None = None) -> tuple[str, list[str]]:
    """Rewrite `:name` bind parameters as `?`; return the SQL and the names in order.

    A list in `values` becomes one `?` per item, for `in (:names)`.
    """
    names: list[str] = []

    def replace(match):
        if match.group(1):
            name = match.group(1)
            names.append(name)
            value = (values or {}).get(name)
            if isinstance(value, list):
                if not value:
                    raise ValueError(f"bind parameter :{name} is an empty list")
                return ", ".join("?" * len(value))
            return "?"
        return match.group(0)

    return _SQL_TOKENS.sub(replace, sql), names


def sql_literal(value) -> str:
    """A value bound to a ?, written in SQL: text in single quotes (' doubled), a number as it is, TRUE/FALSE, NULL,
    DATE 'YYYY-MM-DD', TIMESTAMP 'YYYY-MM-DD HH:MM:SS[.ffffff]'; anything else as the text it is sent as."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float, decimal.Decimal)):
        return str(value)
    if isinstance(value, dt.datetime):  # (sent as its clock time, as jdbc does)
        return f"TIMESTAMP '{value.replace(tzinfo=None).isoformat(' ')}'"
    if isinstance(value, dt.date):
        return f"DATE '{value.isoformat()}'"
    return "'" + str(value).replace("'", "''") + "'"


def written_in(sql: str, literals: list[str]) -> str:
    """The SQL to_qmark gave, with each ? replaced by its value's literal, in order; a ? in quotes or a comment is
    none and stays."""
    values = iter(literals)
    return _SQL_TOKENS.sub(lambda m: next(values, "?") if m.group(2) else m.group(0), sql)
