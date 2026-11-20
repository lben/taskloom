"""The Windows Taskloom's two entries (docs/promises/updates.yaml UP-2, UP-4): the Start menu entry, which opens the
editor, and the Startup entry, which starts the scheduler service at login. Both are shortcuts (.lnk) to a program
folder's taskloomw.exe with `--home "<Taskloom folder>"`, and always start the newest Taskloom: `follow` points them
at a newer program folder (each start of the packaged Windows Taskloom, and an install, call it). Only Tools > Settings
and `taskloom scheduler --at-login` make or remove one (set_entry)."""

from __future__ import annotations

import contextlib
import ctypes
import os
import re
from pathlib import Path

from .scheduler import _lock, _unlock, version_tuple

WINDOWED = "taskloomw.exe"
EDITOR, SCHEDULER = "editor", "scheduler --unless-paused"  # what each entry starts, after --home


def startup() -> Path:
    """The Startup entry (APPDATA's Startup folder: no admin needed)."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("starting the scheduler at login is supported on Windows only")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "Taskloom Scheduler.lnk"


def start_menu() -> Path:
    return startup().parent.parent / "Taskloom.lnk"


def set_entry(path: Path, on: bool, folder: Path, home: Path, command: str) -> Path:
    """Makes the entry `path` start Taskloom's `command` for the Taskloom folder `home` from the program folder
    `folder`, or a newer one it already starts (an older Taskloom never takes it away: UP-4); `on` False removes it."""
    if not on:
        path.unlink(missing_ok=True)
        return path
    with _locked():
        if path.exists() and not _older(path, folder):
            folder = Path(read_link(path)[0]).parent
        _write(path, folder, f'--home "{home}" {command}')
    return path


def follow(folder: Path) -> list[str]:
    """Points each entry there is at Taskloom in `folder` when the program it starts is gone or its folder's VERSION is
    older (none counts as older), keeping its arguments; one that starts this or a newer version is left alone. Today's
    Startup script (Taskloom Scheduler.cmd) is replaced by the Startup entry for its Taskloom folder. Makes no other
    entry. Returns what to tell: the entries whose folder was gone."""
    paths = (start_menu(), startup())
    script = paths[1].with_suffix(".cmd")
    if not script.exists() and not any(p.exists() and _older(p, folder) for p in paths):
        return []
    gone = []
    with _locked():  # (two Taskloom starting at once: the second finds the entries done)
        if script.exists():
            if not paths[1].exists():
                _write(paths[1], folder, f'--home "{_script_home(script)}" {SCHEDULER}')
            script.unlink()
        for path in paths:
            if path.exists() and _older(path, folder):
                target, arguments, _ = read_link(path)
                if not Path(target).exists():
                    gone.append((path, Path(target).parent))
                _write(path, folder, arguments)
    version = version_of(folder)
    return [f"The {'Start menu' if path == paths[0] else 'Startup'} entry started Taskloom from {old}, a folder that "
            f"is gone: it now starts Taskloom {version} in {folder}" for path, old in gone]


def _older(path: Path, folder: Path) -> bool:
    """Whether the entry `path` starts a program that is gone, or one older than the one in `folder`."""
    target = Path(read_link(path)[0])
    return not target.exists() or version_tuple(version_of(target.parent)) < version_tuple(version_of(folder))


def version_of(folder: Path) -> str | None:
    """The version of the Taskloom in a program folder: its VERSION file; None if it has none."""
    try:
        return (folder / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _script_home(script: Path) -> str:
    """The Taskloom folder of the Startup script earlier versions wrote (its `set "TASKLOOM_HOME=..."` line)."""
    found = re.search(r'^set "TASKLOOM_HOME=(.*)"$', script.read_text(encoding="utf-8"), re.MULTILINE)
    if found is None:
        raise ValueError(f"{script}: no TASKLOOM_HOME line")
    return found.group(1).replace("%%", "%")  # (cmd.exe's way of writing a % in a script)


@contextlib.contextmanager
def _locked():
    """One Taskloom at a time changes the entries (the lock is outside <LOCALAPPDATA>/Programs/Taskloom, which holds
    only the versions' folders)."""
    path = Path(os.environ["LOCALAPPDATA"]) / "Taskloom" / "entries.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = _lock(path, wait=True)
    try:
        yield
    finally:
        _unlock(handle)


def _write(path: Path, folder: Path, arguments: str):
    """Writes the entry whole: a reader finds it as it was or as it is now, never half written."""
    new = path.with_name(path.name + ".new")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        write_link(new, folder / WINDOWED, arguments, folder)
        os.replace(new, path)
    finally:
        new.unlink(missing_ok=True)


# --- shortcuts (.lnk), through Windows' own IShellLinkW and IPersistFile (Qt's QFile.link writes no arguments) --------

_SHELL_LINK = "{00021401-0000-0000-C000-000000000046}"
_I_SHELL_LINK_W = "{000214F9-0000-0000-C000-000000000046}"
_I_PERSIST_FILE = "{0000010B-0000-0000-C000-000000000046}"
_TEXT, _SIZE = (ctypes.c_wchar_p, ctypes.c_int), 32768


def read_link(path) -> tuple[str, str, str]:
    """The program, arguments and working folder of the shortcut `path`."""
    def read(link, file):
        _call(file, 5, (ctypes.c_wchar_p, ctypes.c_ulong), str(path), 0)  # IPersistFile::Load, STGM_READ
        target, arguments, workdir = (ctypes.create_unicode_buffer(_SIZE) for _ in range(3))
        _call(link, 3, _TEXT + (ctypes.c_void_p, ctypes.c_ulong), target, _SIZE, None, 0)  # GetPath
        _call(link, 10, _TEXT, arguments, _SIZE)  # GetArguments
        _call(link, 8, _TEXT, workdir, _SIZE)  # GetWorkingDirectory
        return target.value, arguments.value, workdir.value
    return _shell_link(read)


def write_link(path, target, arguments: str, workdir):
    """Saves the shortcut `path` that starts `target` with `arguments` in the folder `workdir`."""
    def write(link, file):
        _call(link, 20, (ctypes.c_wchar_p,), str(target))  # SetPath
        _call(link, 11, (ctypes.c_wchar_p,), arguments)  # SetArguments
        _call(link, 9, (ctypes.c_wchar_p,), str(workdir))  # SetWorkingDirectory
        _call(file, 6, (ctypes.c_wchar_p, ctypes.c_int), str(path), 1)  # IPersistFile::Save
    _shell_link(write)


def _shell_link(use):
    """use(IShellLinkW, IPersistFile) of a new shortcut object."""
    ole32 = ctypes.OleDLL("ole32")  # (a failed HRESULT raises OSError)
    initialized = ctypes.windll.ole32.CoInitialize(None) >= 0  # (already on in another mode: usable all the same)
    link, file = ctypes.c_void_p(), ctypes.c_void_p()
    try:
        ole32.CoCreateInstance(ctypes.byref(_guid(_SHELL_LINK)), None, 1, ctypes.byref(_guid(_I_SHELL_LINK_W)),
                               ctypes.byref(link))  # 1: CLSCTX_INPROC_SERVER
        _call(link, 0, (ctypes.c_void_p, ctypes.c_void_p), ctypes.byref(_guid(_I_PERSIST_FILE)), ctypes.byref(file))
        return use(link, file)
    finally:
        for done in (file, link):
            if done.value:
                _call(done, 2, ())  # Release
        if initialized:
            ctypes.windll.ole32.CoUninitialize()


def _guid(text: str):
    guid = (ctypes.c_byte * 16)()
    ctypes.OleDLL("ole32").CLSIDFromString(text, ctypes.byref(guid))
    return guid


def _call(obj, index: int, types: tuple, *args):
    """Calls the COM method number `index` of `obj`: its vtable's entry (0-2 are IUnknown's, e.g. 2 Release)."""
    method = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0][index]
    return ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *types)(method)(obj, *args)
