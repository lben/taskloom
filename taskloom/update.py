"""Updates of the Windows Taskloom (docs/promises/updates.yaml): each version Taskloom installs is a folder of its own in
<LOCALAPPDATA>/Programs/Taskloom (no admin needed), holding the marker installed-by-taskloom.json; older ones are
removed once nothing runs from them (UP-7)."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from . import entries
from .config import Home
from .scheduler import version_tuple

RELEASE_URL = "https://api.github.com/repos/lben/taskloom/releases/latest"
ASSET = "taskloom-windows-amd64.zip"
HOSTS = {"api.github.com", "github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}
MARKER = "installed-by-taskloom.json"  # {"from": the program folder it was installed from, "files": [its files]}


def programs() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Taskloom"


def clean_up(folder: Path, home: Home) -> list[str]:
    """Removes from programs() what a cut-off download or removal left, and each version Taskloom installed that is
    older than the one in `folder`, other than `folder`, the one it was installed from and one an entry starts. Keeps
    one holding a flow scheduled in `home` or a file of yours (not in its marker's list, or changed after it), and a
    folder you unpacked (no marker). Returns what to tell: what it kept, and why."""
    if not programs().is_dir():
        return []
    this = version_tuple(entries.version_of(folder))
    keep = {_key(folder), *(_key(Path(entries.read_link(p)[0]).parent)
                            for p in (entries.start_menu(), entries.startup()) if p.exists())}
    if (_marker(folder) or {}).get("from"):
        keep.add(_key(_marker(folder)["from"]))
    said = []
    for child in sorted(programs().iterdir()):
        marker = _marker(child)
        if child.name.startswith(".removing-"):
            shutil.rmtree(child, ignore_errors=True)
        elif child.name.startswith(".partial-"):
            _remove(child)
        elif marker is not None and _key(child) not in keep and version_tuple(entries.version_of(child)) < this:
            yours = _yours(child, marker, home)
            if yours:
                said.append(f"Kept the older Taskloom in {child}: it holds {yours}")
            else:
                _remove(child)
    return said


def _yours(folder: Path, marker: dict, home: Home) -> str | None:
    """What in an installed version's folder is yours: a scheduled flow, or a file it was not installed with."""
    for flow in home.scheduled_flows():
        if _key(flow).startswith(_key(folder) + os.sep):
            return f"a scheduled flow, {flow}"
    listed, since = set(marker.get("files") or []), (folder / MARKER).stat().st_mtime_ns
    for path in sorted(folder.rglob("*")):
        if path.is_file() and path != folder / MARKER and (path.relative_to(folder).as_posix() not in listed
                                                            or path.stat().st_mtime_ns > since):
            return f"a file of yours, {path}"
    return None


def _remove(folder: Path):
    """Deletes `folder`, renamed first to .removing-<name>: Windows refuses that while a program or file in it is in use,
    and it is then kept (tried again at the next start); a removal cut off leaves the .removing-* folder."""
    removing = folder.with_name(".removing-" + folder.name)
    try:
        folder.rename(removing)
    except OSError:
        return
    shutil.rmtree(removing, ignore_errors=True)


def _marker(folder: Path) -> dict | None:
    try:
        found = json.loads((folder / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return found if isinstance(found, dict) else None


def _key(path) -> str:
    return os.path.normcase(str(Path(path).resolve()))
