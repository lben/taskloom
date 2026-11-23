"""Updates of the Windows Taskloom (docs/promises/updates.yaml): each version Taskloom installs is a folder of its own in
<LOCALAPPDATA>/Programs/Taskloom (no admin needed), holding the marker installed-by-taskloom.json; older ones are
removed once nothing runs from them (UP-7). Help > Check for updates (the editor) asks GitHub and downloads; what it
gets is checked and unpacked here."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import zipfile
from pathlib import Path, PurePosixPath
from typing import NamedTuple

from . import __version__, entries
from .config import Home
from .scheduler import paused, service_running, version_tuple

RELEASE_URL = "https://api.github.com/repos/lben/taskloom/releases/latest"
ASSET = "taskloom-windows-amd64.zip"
HOSTS = {"api.github.com", "github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}
MARKER = "installed-by-taskloom.json"  # {"from": the program folder it was installed from, "files": [its files]}
TIMEOUT_SECONDS = 30  # a web call that gets no byte for this long ends, saying so (Charter C8)


class Failed(Exception):
    """An update that cannot go on; the message says what went wrong. Nothing was installed."""


def plainly(e: Exception) -> str:
    """What `e` says, with an OSError's paths as they are (str(e) shows them in quotes, a backslash doubled)."""
    if isinstance(e, OSError) and e.strerror and e.filename is not None:
        return f"{e.strerror}: {e.filename}" + (f" -> {e.filename2}" if e.filename2 is not None else "")
    return str(e)


class Release(NamedTuple):
    """The newest release's Windows zip, as GitHub's API tells of it."""
    version: str
    url: str
    size: int
    digest: str  # "sha256:<hex>"; "" when GitHub gives none


def programs() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Taskloom"


def refused(url: str) -> str | None:
    """Why Taskloom does not ask `url`, also as a redirect's target: only Taskloom's GitHub releases, over https (UP-3).
    None: it may."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "https" and parts.hostname in HOSTS:
        return None
    return f"Download refused: {url} is not Taskloom's GitHub releases over https"


def newer_release(answer: bytes) -> Release | None:
    """The newest release, from GitHub's answer to RELEASE_URL, if it is newer than this Taskloom; None if not (an
    older line's fix is never offered)."""
    try:
        found = json.loads(answer)
        version = str(found["tag_name"]).removeprefix("v")
        if not version_tuple(version):
            raise ValueError(f"no version in {found['tag_name']!r}")
        if version_tuple(version) <= version_tuple(__version__):
            return None
        asset = next((a for a in found.get("assets") or [] if a.get("name") == ASSET), None)
        if asset is None:
            raise Failed(f"Taskloom {version} has no {ASSET} to download")
        release = Release(version, str(asset["browser_download_url"]), int(asset["size"]),
                          str(asset.get("digest") or ""))
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise Failed(f"Taskloom's releases gave an answer Taskloom cannot read: {e}") from e
    if refused(release.url):
        raise Failed(refused(release.url))
    return release


def installed(version: str) -> Path | None:
    """The folder of `version` if it is in programs() already (it is used as it is, nothing is downloaded); None if
    not. Taskloom never changes a folder there that is not that version's."""
    folder = programs() / version
    if not folder.exists():
        return None
    if entries.version_of(folder) != version or not (folder / entries.WINDOWED).is_file():
        raise Failed(f"A folder is in the way: {folder}")
    return folder


def partial() -> Path:
    """A new folder for a download, beside the versions' folders (one volume: the version is moved into place by one
    rename). One a cut-off update leaves is removed at the next start (clean_up)."""
    programs().mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=".partial-", dir=programs()))


def unpack(download: Path, release: Release, source: Path, stop, progress) -> Path | None:
    """Checks the downloaded zip `download` whole (UP-3), unpacks it beside itself and moves it into
    programs()/<version> by one rename, with the marker naming `source` (the program folder it updates from) and its
    files. progress(bytes unpacked, of bytes) after each file. None: `stop` (a threading.Event) was set before the
    rename. Raises Failed when a check fails or the disk refuses."""
    try:
        got = download.stat().st_size
        if got != release.size:
            raise Failed(f"The download is incomplete: {got} of {release.size} bytes")
        if release.digest.startswith("sha256:") and _sha256(download) != release.digest.removeprefix("sha256:").lower():
            raise Failed("The download's checksum is not the release's: it is not the zip Taskloom published")
        with zipfile.ZipFile(download) as z:
            members = z.infolist()
            damaged = z.testzip()
            if damaged is not None:
                raise Failed(f"The download is damaged: {damaged} does not read back as it was packed")
            for member in members:
                parts = PurePosixPath(member.filename).parts
                if parts[:1] != ("taskloom",) or ".." in parts or ":" in member.filename or "\\" in member.filename:
                    raise Failed(f"The download holds {member.filename}, outside its taskloom folder")
            names = {member.filename for member in members}
            for needed in (f"taskloom/{entries.WINDOWED}", "taskloom/VERSION"):
                if needed not in names:
                    raise Failed(f"The download has no {needed}")
            said = z.read("taskloom/VERSION").decode("utf-8", "replace").strip()
            if said != release.version:
                raise Failed(f"The download's VERSION says {said}, not the release's {release.version}")
            need, free = sum(m.file_size for m in members), shutil.disk_usage(download.parent).free
            if free < need:
                raise Failed(f"Not enough disk space to unpack Taskloom {release.version}: it needs "
                             f"{need // 2**20 + 1} MB, {free // 2**20} MB are free")
            done = 0
            for member in members:
                if stop.is_set():
                    return None
                z.extract(member, download.parent)
                done += member.file_size
                progress(done, need)
        unpacked = download.parent / "taskloom"
        files = sorted(m.filename.removeprefix("taskloom/") for m in members if not m.is_dir())
        (unpacked / MARKER).write_text(json.dumps({"from": str(source), "files": files}), encoding="utf-8")
        if stop.is_set():
            return None
        folder = programs() / release.version
        os.rename(unpacked, folder)  # (the version's folder is there whole, or not at all)
        return folder
    except zipfile.BadZipFile as e:
        raise Failed(f"The download is damaged: {e}") from e
    except OSError as e:
        raise Failed(f"Could not unpack Taskloom {release.version}: {plainly(e)}") from e


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def put_in_use(folder: Path, home: Home) -> list[str]:
    """Right after an install: the Start menu and Startup entries start the Taskloom in `folder` (UP-2), and when a
    scheduler service runs for `home` and you have not turned it off, that Taskloom's service starts beside it and
    takes it over at a quiet moment (UP-8). Returns what to tell. Writes nothing in the Taskloom folder (UP-5): where
    no service ever ran there is no lock file, and none is made by looking."""
    told = entries.follow(folder)
    if not paused(home) and (home.root / "scheduler.lock").exists() and service_running(home):
        start(folder, home, *entries.SCHEDULER.split())
    return told


def start(folder: Path, home: Home, *command: str):
    """Starts the Taskloom in `folder` on its own (it outlives this one; no console window): taskloomw.exe --home
    <home> <command>."""
    subprocess.Popen([str(folder / entries.WINDOWED), "--home", str(home.root), *command],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=sys.platform != "win32",
                     creationflags=(getattr(subprocess, "DETACHED_PROCESS", 0)
                                    | getattr(subprocess, "CREATE_NO_WINDOW", 0)))


def clean_up(folder: Path, home: Home) -> list[str]:
    """Removes from programs() what a cut-off download or removal left, and each version Taskloom installed that is
    older than the one in `folder`, other than `folder`, the one it was installed from and one an entry or the Startup
    script (one entries.follow kept) starts. Keeps one holding a flow scheduled in `home` or a file of yours (not in its
    marker's list, or changed after it), and a folder you unpacked (no marker). Returns what to tell: what it kept, and
    why."""
    if not programs().is_dir():
        return []
    this = version_tuple(entries.version_of(folder))
    keep = {_key(folder), *(_key(Path(entries.read_link(p)[0]).parent)
                            for p in (entries.start_menu(), entries.startup()) if p.exists())}
    if (_marker(folder) or {}).get("from"):
        keep.add(_key(_marker(folder)["from"]))
    script = entries.startup().with_suffix(".cmd")
    started = os.path.normcase(script.read_text(encoding="utf-8")) if script.exists() else ""
    said = []
    for child in sorted(programs().iterdir()):
        marker = _marker(child)
        if child.name.startswith(".removing-"):
            shutil.rmtree(child, ignore_errors=True)
        elif child.name.startswith(".partial-"):
            _remove(child)
        elif (marker is not None and _key(child) not in keep and os.path.normcase(f"{child}{os.sep}") not in started
              and version_tuple(entries.version_of(child)) < this):
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
