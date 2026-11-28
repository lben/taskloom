"""The tests' stand-in for the system credential store (macOS Keychain, Windows Credential Manager): a keyring backend
that every test and every program a test starts uses (tests/conftest.py chooses it with PYTHON_KEYRING_BACKEND and puts
this folder on PYTHONPATH), so no test ever reaches the real store. Taskloom finds and uses it through keyring as it
does the real one.

FileStore keeps its entries in a JSON file, $TEST_CREDENTIAL_STORE, as {"service/username": value}, in the test's own
folder (conftest's fixture credential_store gives its path). The file holds the values as they are: a test that looks
for a value in the files of its folder leaves this one out, as the real store is not a file of Taskloom's.
$TEST_CREDENTIAL_STORE_MODE, a comma-separated list, makes it behave as a real store can:
- case-insensitive: names that differ only in upper and lower case are one entry, as in Windows' Credential Manager;
- size-limit: an entry over 1,280 UTF-16 units is refused, as Windows refuses one (2,560 bytes), with an error that is
  not a keyring error, as Windows' pywintypes.error is not;
- unavailable: every call fails with keyring.errors.KeyringError, as a locked or missing store does.

WindowsUnderTestName is the real Windows Credential Manager under a made-up service name, $TEST_CREDENTIAL_SERVICE,
whatever service it is asked for. It writes each call it is asked to make to $TEST_CREDENTIAL_STORE (a JSON line each:
the call, the service asked for, the user name), so a test can remove every entry afterwards. Only for the one test of
the real store, on Windows CI.
"""

import json
import os

from keyring.backend import KeyringBackend
from keyring.errors import KeyringError, PasswordDeleteError

WINDOWS_LIMIT = 1280  # UTF-16 units in one entry of Windows' Credential Manager (CRED_MAX_CREDENTIAL_BLOB_SIZE)


class TooLong(Exception):
    """What the size-limit mode raises: as Windows' pywintypes.error, not a keyring error."""


def modes() -> set[str]:
    return {m.strip() for m in os.environ.get("TEST_CREDENTIAL_STORE_MODE", "").split(",") if m.strip()}


def utf16_units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


class FileStore(KeyringBackend):
    priority = 1

    @staticmethod
    def _path() -> str:
        return os.environ["TEST_CREDENTIAL_STORE"]

    def _read(self) -> dict:
        if "unavailable" in modes():
            raise KeyringError("the credential store is locked (the tests' store, in its unavailable mode)")
        if not os.path.exists(self._path()):
            return {}
        with open(self._path(), encoding="utf-8") as f:
            return json.load(f)

    def _write(self, data: dict):
        with open(self._path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)

    @staticmethod
    def _entry(data: dict, service: str, username: str) -> str | None:
        """The key in `data` of the entry (service, username), or None."""
        key = f"{service}/{username}"
        if "case-insensitive" in modes():  # (Windows compares names without regard to case)
            return next((k for k in data if k.upper() == key.upper()), None)
        return key if key in data else None

    def get_password(self, service, username):
        data = self._read()
        entry = self._entry(data, service, username)
        return None if entry is None else data[entry]

    def set_password(self, service, username, password):
        data = self._read()
        if "size-limit" in modes() and utf16_units(password) > WINDOWS_LIMIT:
            raise TooLong(f"{service}/{username}: {utf16_units(password)} UTF-16 units, over the limit of "
                          f"{WINDOWS_LIMIT} of one entry")
        data.pop(self._entry(data, service, username), None)
        data[f"{service}/{username}"] = password
        self._write(data)

    def delete_password(self, service, username):
        data = self._read()
        entry = self._entry(data, service, username)
        if entry is None:
            raise PasswordDeleteError(f"no entry {service}/{username}")
        del data[entry]
        self._write(data)


class WindowsUnderTestName(KeyringBackend):
    priority = 1

    @staticmethod
    def _real():
        from keyring.backends.Windows import WinVaultKeyring

        return WinVaultKeyring()

    @staticmethod
    def _asked(call: str, service: str, username: str) -> str:
        with open(os.environ["TEST_CREDENTIAL_STORE"], "a", encoding="utf-8") as f:
            f.write(json.dumps({"call": call, "service": service, "username": username}) + "\n")
        return os.environ["TEST_CREDENTIAL_SERVICE"]

    def get_password(self, service, username):
        return self._real().get_password(self._asked("get", service, username), username)

    def set_password(self, service, username, password):
        self._real().set_password(self._asked("set", service, username), username, password)

    def delete_password(self, service, username):
        self._real().delete_password(self._asked("delete", service, username), username)
