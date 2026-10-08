"""Per-user files: settings, connections, secrets, calendars and user blocks.

Everything lives under one folder: $TASKLOOM_HOME, or ~/.taskloom by default.
"""

from __future__ import annotations

import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

SETTINGS_DEFAULTS = {"calendar": None, "keep_runs": 20}
SECRET_PREFIX = "secret:"
KEYRING_SERVICE = "taskloom"


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
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
        if keyring is not None:
            value = keyring.get_password(KEYRING_SERVICE, name)
            if value is not None:
                return value
        self._check_secrets_file_permissions()
        value = _load_yaml(self._secrets_file).get(name)
        if value is None:
            raise KeyError(f"secret '{name}' is not set (run: taskloom secret set {name})")
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
