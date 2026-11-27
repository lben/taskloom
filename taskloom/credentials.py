"""Credentials: cards of keys, listed in <Taskloom home>/credentials.yaml (names only, never a value), whose values are
in the system's credential store (macOS Keychain, Windows Credential Manager), through keyring.

The list file: {cards: {CARD: {uses: OTHER (optional), rows: {key: null or "CARD.key"}}}}. A row's null: its value is
in the store, under CARD.key; a text: it points at that row. A card that uses another takes its values from it; its
own row for the same key wins. Names are letters (any language), digits, _ and -, one name whatever the case: the
listed spelling is the one shown and stored, and a reference in any case finds it.

On macOS and Windows a store that cannot be used is said, and nothing is stored: never a plain file there. Elsewhere
(Linux, until stage 6) the values are in secrets.yaml, readable only by you, when there is no store, and a value the
store does not hold is looked for there too.
"""

from __future__ import annotations

import contextlib
import os
import re
import stat
import sys
from pathlib import Path

import yaml

from . import config

SERVICE = "taskloom-cards"  # (earlier versions' secrets, under the service taskloom, are never read)
PART = 500  # characters in one entry at most: one of Windows' holds 1,280 UTF-16 units, a character is 2 at most
ON_A_PC = sys.platform in ("darwin", "win32")
_NAME = re.compile(r"[\w-]+")


class CredentialError(KeyError):
    """A name that cannot be one, a card, key or value that is not there, a circle, a damaged list file, or a store
    that cannot be used; its message says which. A KeyError, as a secret that is not set has always been."""

    def __str__(self):
        return str(self.args[0])


class Circle(CredentialError):
    """Cards that take their values from each other, or rows that point at each other, round to where they began."""


def check_name(name, what: str) -> str:
    """`name`, if it can name a card or a key (`what`); CredentialError saying why not."""
    if not isinstance(name, str) or not name:
        raise CredentialError(f"the {what} has no name" if name in (None, "") else f"{what} {name!r} is not a name")
    if "." in name:
        raise CredentialError(f"the {what} {name!r} holds a dot, which separates the card and the key: use letters, "
                              "digits, _ and -")
    if any(c.isspace() for c in name):
        raise CredentialError(f"the {what} {name!r} holds a space: use letters, digits, _ and -")
    if not _NAME.fullmatch(name):
        raise CredentialError(f"the {what} {name!r} holds a character a name cannot have: use letters, digits, _ and -")
    return name


def split(reference: str) -> tuple[str, str]:
    """CARD.key as (CARD, key), both names checked; CredentialError saying why it is not a reference."""
    card, dot, key = reference.partition(".")
    if not dot:
        raise CredentialError(f"{reference!r} is not a credential: a reference needs CARD.key, a card and one of its "
                              "keys (e.g. UAT.password)")
    try:
        return check_name(card, "card"), check_name(key, "key")
    except CredentialError as e:
        raise CredentialError(f"{reference}: {e}") from None


# --- the list file ------------------------------------------------------------------------------------------------

def cards(root: Path) -> dict:
    """The list file's cards, in its order: {CARD: {"uses": OTHER or None, "rows": {key: None or "CARD.key"}}}; {} when
    there is none. CredentialError naming the file if it cannot be read or is damaged."""
    path = root / "credentials.yaml"
    try:
        listed = config._load_yaml(path).get("cards") or {}
    except (ValueError, OSError) as e:
        raise CredentialError(f"the list of credentials cannot be read: {e}") from None
    try:
        if not isinstance(listed, dict):
            raise CredentialError("cards: write each card as its name and a colon, on a line of its own")
        found = {}
        for card, entry in listed.items():
            entry = entry or {}
            rows = entry.get("rows") or {} if isinstance(entry, dict) else None
            uses = entry.get("uses") if isinstance(entry, dict) else None
            if not isinstance(rows, dict):
                raise CredentialError(f"card {card}: write its rows under rows:, each key: null, or key: CARD.key")
            if _find(found, check_name(card, "card")) is not None:
                raise CredentialError(f"the card {card} is listed twice (upper and lower case are one name)")
            if uses is not None:
                check_name(uses, "card it uses")
            keys = {}
            for key, pointer in rows.items():
                if _find(keys, check_name(key, "key")) is not None:
                    raise CredentialError(f"card {card}: the key {key} is listed twice (upper and lower case are one "
                                          "name)")
                if pointer is not None:
                    split(pointer if isinstance(pointer, str) else repr(pointer))
                keys[key] = pointer
            found[card] = {"uses": uses, "rows": keys}
        return found
    except CredentialError as e:
        raise CredentialError(f"{path} is damaged: {e}") from None


def _find(names, name: str) -> str | None:
    """The listed spelling of `name` among `names` (upper and lower case are one name), or None."""
    return next((n for n in names if n.casefold() == name.casefold()), None)


def _listed_text(listed: dict) -> str:
    return yaml.safe_dump({"cards": {name: ({"uses": c["uses"]} if c["uses"] else {}) | {"rows": c["rows"]}
                                     for name, c in listed.items()}}, sort_keys=False, allow_unicode=True)


# --- following a reference ----------------------------------------------------------------------------------------

def holder(listed: dict, reference: str) -> str:
    """The row, CARD.key in listed spelling, that holds the value `reference` names: the card's own row, a pointer
    followed, or the row of the card it uses, to the end. CredentialError if a card or key is missing, or for a
    circle (as its names are listed)."""
    card, key = split(reference)
    seen, path = {}, []  # (the rows passed: where each is in path, how it was left)
    while True:
        here = _find(listed, card)
        row = here and _find(listed[here]["rows"], key)
        at = f"{here or card}.{row or key}"
        if at.casefold() in seen:
            circle = path[seen[at.casefold()]:] + [path[seen[at.casefold()]]]
            raise Circle(f"Circle in credentials: {' -> '.join(circle)}")
        seen[at.casefold()] = len(path)
        if row is not None and listed[here]["rows"][row] is None:
            return at
        if row is not None:
            path.append(at)
            card, key = split(listed[here]["rows"][row])
        elif here is not None and listed[here]["uses"]:
            path.append(here)
            card = listed[here]["uses"]
        else:
            raise _not_set(reference, at)


def circle(listed: dict) -> str | None:
    """What holder says of the first circle in `listed` (as cards() gives it), or None if there is none."""
    keys = {key.casefold(): key for c in listed.values() for key in c["rows"]}
    absent = next(f"k{n}" for n in range(len(keys) + 1) if f"k{n}" not in keys)  # (follows the cards used to the end)
    for card in listed:
        for key in [*keys.values(), absent]:
            try:
                holder(listed, f"{card}.{key}")
            except Circle as e:
                return str(e)
            except CredentialError:
                pass
    return None


def _not_set(reference: str, at: str) -> CredentialError:
    if at.casefold() == reference.casefold():
        return CredentialError(f"secret '{at}' is not set (run: taskloom secret set {at})")
    return CredentialError(f"secret '{reference}' is not set: it comes from {at}, which is not set (run: taskloom "
                           f"secret set {at})")


def value(root: Path, reference: str) -> str:
    """The value `reference` (CARD.key) names. CredentialError if there is none, or the store cannot be used."""
    at = holder(cards(root), reference)
    found = stored(root, at)
    if found is None:
        raise _not_set(reference, at)
    return found


# --- the store ----------------------------------------------------------------------------------------------------

def _store():
    """keyring, or None where there is no credential store (then secrets.yaml); on a PC, CredentialError instead."""
    found = config._keyring()
    if found is None and ON_A_PC:
        raise _cannot("no credential store was found")
    return found


def _cannot(reason: str) -> CredentialError:
    return CredentialError(f"The credential store cannot be used: {reason}")


@contextlib.contextmanager
def _asking_the_store():
    """What the store raises (keyring's errors, or the system's own, as Windows' pywintypes.error) as CredentialError."""
    try:
        yield
    except Exception as e:
        raise _cannot(str(e) or type(e).__name__) from None


def _entry(name: str, part: int) -> str:
    return name if part == 1 else f"{name}#{part}"  # (no name holds a #: a part is never another row's entry)


def _get(keyring, name: str) -> str | None:
    """The value stored under `name`, its parts joined, or None."""
    parts = []
    while (part := keyring.get_password(SERVICE, _entry(name, len(parts) + 1))) is not None:
        parts.append(part)
    return "".join(parts) if parts else None


def _put(keyring, name: str, value: str | None):
    """`value` stored under `name`, in parts of PART characters at most; None: every part removed."""
    parts = [] if value is None else [value[i:i + PART] for i in range(0, len(value), PART)] or [""]
    for n, part in enumerate(parts, 1):
        keyring.set_password(SERVICE, _entry(name, n), part)
    n = len(parts) + 1
    while keyring.get_password(SERVICE, _entry(name, n)) is not None:  # (the parts of a longer value before)
        keyring.delete_password(SERVICE, _entry(name, n))
        n += 1


def version(root: Path) -> tuple | None:
    """Which writing of the list file is there now (each writing replaces the file: _write_whole); None: none."""
    try:
        found = (root / "credentials.yaml").stat()
    except FileNotFoundError:
        return None
    return found.st_ino, found.st_mtime_ns, found.st_size


def stored(root: Path, name: str) -> str | None:
    """The value stored under `name` (CARD.key as listed), or None."""
    keyring = _store()
    with _asking_the_store():
        found = None if keyring is None else _get(keyring, name)
    if found is None and not ON_A_PC:
        found = _file_values(root).get(name)
    return None if found is None else str(found)


def save(root: Path, listed: dict | None, values: dict) -> str:
    """Each of `values` ({CARD.key: value, or None: removed}) stored, then the list file written as `listed` (None: as
    it is). All or nothing: if a step fails, the values already changed are put back and its error raised. Returns
    where the values are."""
    keyring = _store()
    if keyring is None:
        before = {name: config._load_yaml(root / "secrets.yaml").get(name) for name in values}
    else:
        with _asking_the_store():
            before = {name: _get(keyring, name) for name in values}
    changed = []
    try:
        for name, new in values.items():
            changed.append(name)
            _write(root, keyring, name, new)
        if listed is not None:
            root.mkdir(parents=True, exist_ok=True)
            config._write_whole(root / "credentials.yaml", _listed_text(listed))
    except BaseException:
        for name in changed:
            with contextlib.suppress(Exception):
                _write(root, keyring, name, before[name])
        raise
    return "the system credential store" if keyring is not None else str(root / "secrets.yaml")


def _write(root: Path, keyring, name: str, new: str | None):
    if keyring is not None:
        with _asking_the_store():
            _put(keyring, name, new)
    if keyring is None or new is None and not ON_A_PC:  # (a value removed: from secrets.yaml too, where it can be)
        _file_write(root, name, new)


# --- secrets.yaml (not on a PC) -------------------------------------------------------------------------------------

def _file_values(root: Path) -> dict:
    path = root / "secrets.yaml"
    if sys.platform != "win32" and path.exists() and path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise PermissionError(f"{path} must be readable only by you (chmod 600)")
    return config._load_yaml(path)


def _file_write(root: Path, name: str, new: str | None):
    path = root / "secrets.yaml"
    data = config._load_yaml(path)
    if new is None and name not in data:
        return
    if new is None:
        del data[name]
    else:
        data[name] = new
    root.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        os.chmod(path, 0o600)  # the mode above is only for a new file: also one made by hand
        yaml.safe_dump(data, f)


# --- taskloom secret set | delete | list --------------------------------------------------------------------------

def store(root: Path, reference: str, new: str) -> str:
    """`new` stored as CARD.key's value; the card and row are listed if they were not (a pointer there becomes the
    row's own value). Returns where the value is."""
    card, key = split(reference)
    listed = cards(root)
    here = _find(listed, card) or card
    rows = listed.setdefault(here, {"uses": None, "rows": {}})["rows"]
    row = _find(rows, key) or key
    unlisted = row not in rows or rows[row] is not None
    rows[row] = None
    return save(root, listed if unlisted else None, {f"{here}.{row}": new})


def remove(root: Path, reference: str):
    """CARD.key's row, or the whole card CARD, taken off the list and its values out of the store. CredentialError if
    it is not there, or another card uses it or a row points at it (naming them)."""
    card, dot, key = reference.partition(".")
    card, key = split(reference) if dot else (check_name(card, "card"), None)
    listed = cards(root)
    here = _find(listed, card)
    if here is None:
        raise CredentialError(f"there is no card {card} (taskloom secret list shows the cards)")
    rows = listed[here]["rows"]
    row = key and _find(rows, key)
    if key and row is None:
        raise CredentialError(f"the card {here} has no key {key} (taskloom secret list shows the cards and keys)")
    found = users(listed, here, row)
    if found:
        raise CredentialError(f"{reference} is not deleted, it is used: {'; '.join(found)}")
    gone = [row] if row else list(rows)
    if row:
        del rows[row]
    else:
        del listed[here]
    save(root, listed, {f"{here}.{k}": None for k in gone})


def users(listed: dict, card: str, row: str | None = None) -> list[str]:
    """Who uses the row `row` of `card` (None: the whole card), as listed spellings: the cards that take their values
    from the card, and the rows (not its own that go with it) that point at it."""
    gone = [row] if row else list(listed[card]["rows"])
    found = [f"{name} takes its values from {card}" for name, c in listed.items()
             if row is None and name != card and c["uses"] and c["uses"].casefold() == card.casefold()]
    for name, c in listed.items():
        for k, pointer in c["rows"].items():
            if pointer is not None and not (name == card and k in gone):
                to_card, to_key = split(pointer)
                if to_card.casefold() == card.casefold() and (row is None or to_key.casefold() == row.casefold()):
                    found.append(f"{name}.{k} points at {pointer}")
    return found


def listing(root: Path) -> list[str]:
    """`taskloom secret list`'s lines: each card (and the card it takes its values from), each of its keys, a pointer
    as key -> CARD.key, and "no value stored" for a key whose value is not there. Never a value."""
    listed = cards(root)
    if not listed:
        return ["no cards yet: store a value with taskloom secret set CARD.key"]
    lines = []
    for name, c in listed.items():
        lines.append(name + (f" (takes its values from {c['uses']})" if c["uses"] else ""))
        for key, pointer in c["rows"].items():
            lines.append(f"  {key} -> {pointer}" if pointer is not None else
                         f"  {key}" + ("" if stored(root, f"{name}.{key}") is not None else "  (no value stored)"))
    return lines
