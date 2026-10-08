"""How to start Taskloom's own command line as a new process."""

from __future__ import annotations

import sys


def runner_command() -> list[str]:
    return [sys.executable, "-m", "taskloom.cli"]
