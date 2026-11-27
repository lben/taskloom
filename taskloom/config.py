"""Per-user files: settings, connections, secrets, calendars and user blocks.

Everything lives under one folder: $TASKLOOM_HOME, or ~/.taskloom by default.
"""

from __future__ import annotations

import calendar
import datetime as dt
import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

SETTINGS_DEFAULTS = {
    "calendar": None, "java_home": None, "max_concurrent_runs": None, "keeper": [],
    "notify_email": None, "notify_on": ["failure"], "smtp_connection": None, "python": None, "python_env": None,
    "keep_runs_for": "5 years",  # runs (their history, logs and files) that started longer ago are deleted
}
SECRET_PREFIX = "secret:"


class Seen(set):
    """Values to hide (REVEALED, engine.HIDDEN) that tells each of its `told` (functions) of a value added, before
    code gets that value: so a run's relays (cli._hide_below_print) hide it in what code writes below print too."""

    def __init__(self):
        super().__init__()
        self.told = []

    def add(self, value):
        if value not in self:
            super().add(value)
            for tell in list(self.told):
                tell(value)


# Every secret value this process has seen; run output is scrubbed of them (see engine.redact).
REVEALED: set[str] = Seen()


PERIOD = r"([1-9][0-9]*) *(day|month|year)s?"  # e.g. 18 months, 1 year, 90 days


def keep_runs_for(settings: dict) -> tuple[int, str]:
    """keep_runs_for as (number, unit), e.g. '18 months' -> (18, 'months'); ValueError if it cannot be read."""
    value = settings["keep_runs_for"]
    match = re.fullmatch(PERIOD, str(value).strip())
    if match is None:
        raise ValueError(f"settings.yaml: keep_runs_for must be a whole number (1 or more) of days, months or years, "
                         f"e.g. 5 years, 18 months or 90 days; got {value!r}")
    return int(match[1]), match[2] + "s"


def kept_runs(value) -> tuple[str, int]:
    """A start block's Keep runs as its flow file has it: 'all', 'last 100' or a time such as '18 months' (also
    day, month, year) -> ('all', 0), ('last', 100), ('months', 18); ValueError if it cannot be read."""
    match = re.fullmatch(rf"(all)|last *([1-9][0-9]*)|{PERIOD}", str(value).strip())
    if match is None:
        raise ValueError(f"cannot read {value!r}: write all, last N, or N days, months or years, with N a whole "
                         "number (1 or more), e.g. last 100 or 18 months")
    if match[1]:
        return "all", 0
    if match[2]:
        return "last", int(match[2])
    return match[4] + "s", int(match[3])


def runs_kept_since(settings: dict) -> dt.datetime:
    """Runs kept as in Settings that started before this are deleted: keep_runs_for before now."""
    return before_now(*keep_runs_for(settings))


def before_now(number: int, unit: str) -> dt.datetime:
    """`number` days, months or years before now (months and years as on a calendar)."""
    now = dt.datetime.now()
    try:
        if unit == "days":
            return now - dt.timedelta(days=number)
        year, month = divmod(now.year * 12 + now.month - 1 - number * (12 if unit == "years" else 1), 12)
        return now.replace(year=year, month=month + 1, day=min(now.day, calendar.monthrange(year, month + 1)[1]))
    except (OverflowError, ValueError):  # further back than dates go: every run is kept
        return dt.datetime.min


def _write_whole(path: Path, text: str):
    """Writes the file whole: written beside it first, then put in its place in one step, so a reader sees it as it was
    or as it is now. On Windows a file another program has open just then cannot be replaced: it tries again for a
    second, then raises OSError saying so (the file is as it was)."""
    import tempfile
    import time

    fd, part = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        for attempt in range(20):
            try:
                os.replace(part, path)
                return
            except PermissionError:  # (Windows: open in another program this instant)
                time.sleep(0.05)
        raise OSError(f"{path} could not be saved: another program kept it open; nothing was changed, try again")
    finally:
        Path(part).unlink(missing_ok=True)


def _load_yaml(path: Path) -> dict:
    """The file's mapping; {} if there is no file. On Windows a file being replaced this instant (_write_whole) cannot
    be read: it is read again for a second, as its writer tries again, then PermissionError."""
    import time

    if not path.exists():
        return {}
    for attempt in range(20):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            break
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.05)
        except UnicodeDecodeError as e:  # e.g. UTF-16, as Windows PowerShell writes
            raise ValueError(f"{path}: not UTF-8 text, save it as UTF-8 ({e})") from None
        except yaml.YAMLError as e:
            raise ValueError(f"{path}: {e}") from None
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping at the top level")
    return data


@dataclass
class Home:
    root: Path

    @classmethod
    def default(cls) -> "Home":
        return cls(Path(os.environ.get("TASKLOOM_HOME") or Path.home() / ".taskloom").resolve())

    @property
    def blocks_dir(self) -> Path:
        return self.root / "blocks"

    @property
    def calendars_dir(self) -> Path:
        return self.root / "calendars"

    @property
    def runs_dir(self) -> Path:
        return self.root / "runs"

    @property
    def history_db(self) -> Path:
        return self.root / "history.db"

    def scheduled_flows(self) -> list[Path]:
        """Flows the scheduler runs, listed in scheduled.yaml (see scheduled_list)."""
        return self.scheduled_list()[0]

    def scheduled_list(self) -> tuple[list[Path], list]:
        """The flows listed in scheduled.yaml, and the entries in it that are not a flow file's path (e.g. an empty
        item or a number, left by a hand edit), which are left out. ValueError if it cannot be read."""
        path = self.root / "scheduled.yaml"
        flows = _load_yaml(path).get("flows") or []
        if not isinstance(flows, list):
            raise ValueError(f"{path}: flows must be a list of flow files, each on a line of its own after a -; "
                             f"got {flows!r}")
        listed = [p for p in flows if isinstance(p, str) and p.strip()]
        return [Path(p) for p in listed], [p for p in flows if p not in listed]

    def set_scheduled(self, flow_path: Path, enabled: bool):
        """Schedules the flow, or takes it off. scheduled.yaml is written whole: the scheduler service, which reads it
        every second, sees it as it was or as it is now, never half-written. OSError if it cannot be saved (it is then
        as it was)."""
        flows = [p for p in self.scheduled_flows() if p != flow_path] + ([flow_path] if enabled else [])
        self.root.mkdir(parents=True, exist_ok=True)
        _write_whole(self.root / "scheduled.yaml", yaml.safe_dump({"flows": [str(p) for p in flows]}))

    def settings(self) -> dict:
        data = _load_yaml(self.root / "settings.yaml")
        unknown = set(data) - set(SETTINGS_DEFAULTS)
        if unknown:
            raise ValueError(f"settings.yaml: unknown keys {sorted(unknown)}")
        return {**SETTINGS_DEFAULTS, **data}

    def connections(self) -> dict:
        data = _load_yaml(self.root / "connections.yaml")
        for name, conn in data.items():
            if not isinstance(conn, dict) or "kind" not in conn:
                raise ValueError(f"connections.yaml: connection '{name}' needs a 'kind'")
        return data

    # --- secrets: credentials (see credentials.py) -----------------------------

    def get_secret(self, reference: str) -> str:
        """The value of the credential CARD.key; every such value is hidden in what a run tells (REVEALED).
        credentials.CredentialError (a KeyError) if there is none or the credential store cannot be used."""
        from .credentials import value

        found = value(self.root, reference)
        REVEALED.add(found)
        return found

    def set_secret(self, reference: str, value: str) -> str:
        """Stores the credential CARD.key (listing its card and key if they are not); returns where it is."""
        from .credentials import store

        return store(self.root, {reference: value})

    def resolve_secrets(self, value):
        """Replace "secret:CARD.key" strings (also inside dicts and lists) with the credential's value."""
        if isinstance(value, str) and value.startswith(SECRET_PREFIX):
            return self.get_secret(value[len(SECRET_PREFIX):])
        if isinstance(value, dict):
            return {k: self.resolve_secrets(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.resolve_secrets(v) for v in value]
        return value


def _keyring():
    """The keyring module if a real credential store backs it, else None."""
    try:
        import keyring
        from keyring.backends import fail
    except ImportError:
        return None
    backend = keyring.get_keyring()
    if isinstance(backend, fail.Keyring) or type(backend).__name__ == "ChainerBackend" and not backend.backends:
        return None
    return keyring
