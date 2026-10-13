"""Keeping work safe: a crash log, autosaved copies of unsaved flows, and noticing a crash.

Unsaved changes are copied to <Taskloom folder>/recovery/ shortly after each edit (the flow
file itself is only written when you save). Each editor process marks itself as running;
if one disappears without closing normally, the next editor recovers its copies.
"""

from __future__ import annotations

import datetime as dt
import faulthandler
import os
import sys
import threading
import traceback
from pathlib import Path

import yaml

CRASH_LOG = "crash.log"


def pid_alive(pid: int) -> bool:
    if pid == os.getpid():
        return True
    if sys.platform == "win32":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Safety:
    def __init__(self, root: Path):
        self.root = root
        self.recovery_dir = root / "recovery"
        self.recovery_dir.mkdir(parents=True, exist_ok=True)
        self.marker = self.recovery_dir / f"editor-{os.getpid()}.running"
        self._log_file = None

    # --- crash log ---------------------------------------------------------------

    def install_crash_log(self, on_error=None):
        """Low-level crashes, uncaught Python errors and Qt's own fatal messages go to crash.log."""
        self._log_file = open(self.root / CRASH_LOG, "a", encoding="utf-8")
        faulthandler.enable(file=self._log_file, all_threads=True)

        def log_exception(kind, exc_type, exc, tb):
            text = "".join(traceback.format_exception(exc_type, exc, tb))
            self.log(f"{kind}:\n{text}")
            if on_error is not None:
                on_error(text)

        sys.excepthook = lambda t, e, tb: log_exception("uncaught error", t, e, tb)
        threading.excepthook = lambda a: log_exception(f"uncaught error in thread {a.thread.name}",
                                                      a.exc_type, a.exc_value, a.exc_traceback)
        try:
            from PySide6.QtCore import QtMsgType, qInstallMessageHandler

            def qt_messages(mode, context, message):
                if mode in (QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
                    self.log(f"Qt: {message}")

            qInstallMessageHandler(qt_messages)
        except ImportError:
            pass

    def log(self, text: str):
        if self._log_file is not None:
            self._log_file.write(f"--- {dt.datetime.now():%Y-%m-%d %H:%M:%S} (process {os.getpid()})\n{text}\n")
            self._log_file.flush()

    # --- this editor is running -----------------------------------------------------

    def start(self) -> list[int]:
        """Mark this editor as running; return the processes of editors that did not close normally."""
        crashed = []
        for marker in self.recovery_dir.glob("editor-*.running"):
            pid = int(marker.stem.split("-")[1].split(".")[0])
            if not pid_alive(pid):
                crashed.append(pid)
                marker.unlink(missing_ok=True)
        self.marker.write_text(str(os.getpid()))
        return crashed

    def stop(self):
        """A normal exit: nothing to recover from this process."""
        self.marker.unlink(missing_ok=True)

    # --- autosaved copies -------------------------------------------------------------

    def copy_path(self, tab_id: str) -> Path:
        return self.recovery_dir / f"{os.getpid()}-{tab_id}.yaml"

    def write_copy(self, tab_id: str, flow_path, data: dict):
        target = self.copy_path(tab_id)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(yaml.safe_dump({"path": str(flow_path) if flow_path else None,
                                       "saved": dt.datetime.now().isoformat(timespec="seconds"),
                                       "data": data}, sort_keys=False, allow_unicode=True), encoding="utf-8")
        os.replace(tmp, target)  # never leave a half-written copy

    def drop_copy(self, tab_id: str):
        self.copy_path(tab_id).unlink(missing_ok=True)

    def orphaned_copies(self) -> list[dict]:
        """Copies left by editors that are no longer running, oldest first."""
        found = []
        for path in sorted(self.recovery_dir.glob("*.yaml"), key=lambda p: p.stat().st_mtime):
            owner = path.name.split("-", 1)[0]
            if owner.isdigit() and pid_alive(int(owner)):
                continue
            try:
                entry = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except (OSError, yaml.YAMLError):
                continue
            if isinstance(entry.get("data"), dict):
                found.append({**entry, "file": path})
        return found
