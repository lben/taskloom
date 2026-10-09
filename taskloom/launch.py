"""How to start Taskloom's own programs as new processes, from source or from a packaged build.

A packaged build has two programs side by side: `taskloom` (command line, used for runs
and the scheduler) and `taskloomw` (no console window; opens the editor by default).
"""

from __future__ import annotations

import sys
from pathlib import Path

EXE = ".exe" if sys.platform == "win32" else ""


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def runner_command() -> list[str]:
    """The command line program; it prints to a console, so runs report back through stdout."""
    if frozen():
        return [str(Path(sys.executable).with_name(f"taskloom{EXE}"))]
    return [sys.executable, "-m", "taskloom.cli"]


def windowed_command() -> list[str]:
    """For things started with no console window: the scheduler at login, the editor from the tray."""
    if frozen():
        windowed = Path(sys.executable).with_name(f"taskloomw{EXE}")
        return [str(windowed if windowed.exists() else Path(sys.executable).with_name(f"taskloom{EXE}"))]
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return [str(pythonw if pythonw.exists() else sys.executable), "-m", "taskloom.cli"]
