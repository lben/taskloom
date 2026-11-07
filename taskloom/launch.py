"""How to start Taskloom's own programs as new processes, from source or from a packaged build.

A packaged build has two programs side by side: `taskloom` (command line, used for runs
and the scheduler) and `taskloomw` (no console window; opens the editor by default).
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

EXE = ".exe" if sys.platform == "win32" else ""
ARGS_VARIABLE = "TASKLOOM_ARGS"  # the command line program's arguments (a JSON list), instead of its own


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


def pid_alive(pid: int) -> bool:
    """Whether the process `pid` is running (e.g. a run's, recorded in its history)."""
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


def run_alive(pid: int | None, began: float | None) -> bool:
    """Whether a run is still going, from its run history (History.running): its process `pid` runs, and it is the
    run's, as it began by `began`, the moment the run began (seconds since 1970). Once a process has ended, another
    program may get its number (after a restart, any program). Not known (None): not going."""
    since = process_began(pid) if pid and isinstance(began, (int, float)) and pid_alive(pid) else None
    return since is not None and since <= began


def process_began(pid: int) -> float | None:
    """When the process `pid` began (seconds since 1970, UTC); None if that cannot be told."""
    import ctypes

    try:
        if sys.platform == "win32":
            from ctypes import wintypes

            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return None
            times = [wintypes.FILETIME() for _ in range(4)]  # creation, exit, kernel, user
            try:
                if not ctypes.windll.kernel32.GetProcessTimes(handle, *map(ctypes.byref, times)):
                    return None
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
            return (times[0].dwHighDateTime << 32 | times[0].dwLowDateTime) / 10 ** 7 - 11644473600  # (from 1601)
        if sys.platform.startswith("linux"):  # ticks after the boot, which /proc/stat gives in seconds since 1970
            ticks = int(Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()[19])
            boot = next(int(line.split()[1]) for line in Path("/proc/stat").read_text().splitlines()
                        if line.startswith("btime "))
            return boot + ticks / os.sysconf("SC_CLK_TCK")
        if sys.platform == "darwin":  # sysctl kern.proc.pid.<pid>: a kinfo_proc, which starts with when it began
            sysctl = ctypes.CDLL(None).sysctl
            sysctl.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_uint, ctypes.c_void_p,
                               ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
            info, size = ctypes.create_string_buffer(1024), ctypes.c_size_t(1024)
            if sysctl((ctypes.c_int * 4)(1, 14, 1, pid), 4, info, ctypes.byref(size), None, 0) or size.value < 16:
                return None  # (none: no such process)
            seconds, microseconds = struct.unpack_from("qi", info)  # struct timeval
            return seconds + microseconds / 10 ** 6
    except (OSError, ValueError, IndexError, StopIteration):  # e.g. it has just ended
        return None
    return None
