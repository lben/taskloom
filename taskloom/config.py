"""Per-user files: settings, connections, secrets, calendars and user blocks.

Everything lives under one folder: $TASKLOOM_HOME, or ~/.taskloom by default.
"""

from __future__ import annotations

import calendar
import datetime as dt
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

SETTINGS_DEFAULTS = {
    "calendar": None, "java_home": None, "max_concurrent_runs": None, "keeper": [],
    "notify_email": None, "notify_on": ["failure"], "smtp_connection": None, "python": None, "python_env": None,
    "keep_runs_for": "5 years",  # runs (their history, logs and files) that started longer ago are deleted
}
SECRET_PREFIX = "secret:"
# Every secret value this process has seen; run output is scrubbed of them (see engine.redact).
REVEALED: set[str] = set()
KEYRING_SERVICE = "taskloom"


def keep_runs_for(settings: dict) -> tuple[int, str]:
    """keep_runs_for as (number, unit), e.g. '18 months' -> (18, 'months'); ValueError if it cannot be read."""
    value = settings["keep_runs_for"]
    match = re.fullmatch(r"([1-9][0-9]*) *(day|month|year)s?", str(value).strip())
    if match is None:
        raise ValueError(f"settings.yaml: keep_runs_for must be a whole number (1 or more) of days, months or years, "
                         f"e.g. 5 years, 18 months or 90 days; got {value!r}")
    return int(match[1]), match[2] + "s"


def runs_kept_since(settings: dict) -> dt.datetime:
    """Runs that started before this are deleted: keep_runs_for before now (months and years as on a calendar)."""
    number, unit = keep_runs_for(settings)
    now = dt.datetime.now()
    try:
        if unit == "days":
            return now - dt.timedelta(days=number)
        year, month = divmod(now.year * 12 + now.month - 1 - number * (12 if unit == "years" else 1), 12)
        return now.replace(year=year, month=month + 1, day=min(now.day, calendar.monthrange(year, month + 1)[1]))
    except (OverflowError, ValueError):  # further back than dates go: every run is kept
        return dt.datetime.min


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
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
        """Flows the scheduler runs, listed in scheduled.yaml."""
        data = _load_yaml(self.root / "scheduled.yaml")
        return [Path(p) for p in data.get("flows") or []]

    def set_scheduled(self, flow_path: Path, enabled: bool):
        flows = [p for p in self.scheduled_flows() if p != flow_path] + ([flow_path] if enabled else [])
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "scheduled.yaml").write_text(yaml.safe_dump({"flows": [str(p) for p in flows]}), encoding="utf-8")

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

    # --- secrets -------------------------------------------------------------
    # The OS credential store (Windows Credential Manager, macOS Keychain) when one is
    # usable; otherwise a secrets.yaml readable only by the user (headless Linux).

    @property
    def _secrets_file(self) -> Path:
        return self.root / "secrets.yaml"

    def get_secret(self, name: str) -> str:
        keyring = _keyring()
        value = keyring.get_password(KEYRING_SERVICE, name) if keyring is not None else None
        if value is None:
            self._check_secrets_file_permissions()
            value = _load_yaml(self._secrets_file).get(name)
        if value is None:
            raise KeyError(f"secret '{name}' is not set (run: taskloom secret set {name})")
        REVEALED.add(str(value))
        return str(value)

    def set_secret(self, name: str, value: str) -> str:
        keyring = _keyring()
        if keyring is not None:
            keyring.set_password(KEYRING_SERVICE, name, value)
            return "the system credential store"
        self.root.mkdir(parents=True, exist_ok=True)
        data = _load_yaml(self._secrets_file)
        data[name] = value
        fd = os.open(self._secrets_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            os.chmod(self._secrets_file, 0o600)  # the mode above is only for a new file: also one made by hand
            yaml.safe_dump(data, f)
        return str(self._secrets_file)

    def _check_secrets_file_permissions(self):
        if sys.platform != "win32" and self._secrets_file.exists():
            if self._secrets_file.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
                raise PermissionError(f"{self._secrets_file} must be readable only by you (chmod 600)")

    def resolve_secrets(self, value):
        """Replace "secret:NAME" strings (also inside dicts and lists) with the secret."""
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
