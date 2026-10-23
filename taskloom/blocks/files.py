"""File blocks: copy, move and delete between local folders (incl. network shares) and servers.

Each path has a setting saying where it is: on this computer (C:/data/x.csv, \\\\server\\share\\x.csv,
/home/me/x.csv) when left empty, or on the server of an SSH connection (~/app/config.yaml on appserver).
A wildcard is allowed in the last part of a source path: ~/logs/*.log.
"""

from __future__ import annotations

import fnmatch
import functools
import glob
import os
import posixpath
import shutil
import stat
from collections import Counter
from contextlib import ExitStack

from .. import ssh
from ..block import IDENTIFIER, Block, fields, ports

CHUNK = 1 << 20


class LocalFiles:
    sep_join = staticmethod(os.path.join)

    def expand(self, path: str) -> list[str]:
        path = os.path.expanduser(path)
        if os.path.exists(path):  # an existing name such as report[1].csv is that file, not a pattern
            return [path]
        return sorted(glob.glob(path)) if glob.has_magic(path) else []

    def norm(self, path: str) -> str:
        return os.path.expanduser(path)

    def key(self, path: str) -> str:
        """The path to compare locations by: in full, with links resolved (and in lower case on Windows)."""
        return os.path.normcase(os.path.realpath(path))

    def is_dir(self, path: str) -> bool:
        return os.path.isdir(path)

    def exists(self, path: str) -> bool:
        return os.path.exists(path)

    def walk(self, root: str):
        """(path, path relative to root, is a folder) for every folder and file under root."""
        for folder, _, names in os.walk(root):
            if folder != root:
                yield folder, os.path.relpath(folder, root), True
            for name in names:
                full = os.path.join(folder, name)
                yield full, os.path.relpath(full, root), False

    def makedirs(self, folder: str):
        os.makedirs(folder, exist_ok=True)

    def open(self, path: str, mode: str):
        if "w" in mode:
            self.makedirs(os.path.dirname(path) or ".")
        return open(path, mode)

    def remove(self, path: str):
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.remove(path)  # a link goes, never what it points to

    def close(self):
        pass


class ServerFiles:
    sep_join = staticmethod(posixpath.join)

    def __init__(self, client):
        self.client = client
        self.sftp = client.open_sftp()

    def norm(self, path: str) -> str:
        return ssh.sftp_path(self.sftp, path)

    def _stat(self, path):
        try:
            return self.sftp.stat(path)
        except FileNotFoundError:
            return None

    def expand(self, path: str) -> list[str]:
        path = self.norm(path)
        if self._stat(path):  # an existing name such as report[1].csv is that file, not a pattern
            return [path]
        folder, name = posixpath.split(path)
        if not glob.has_magic(name):
            return []
        try:
            names = self.sftp.listdir(folder or ".")
        except FileNotFoundError:  # no such folder: nothing found, as on this computer
            return []
        return sorted(posixpath.join(folder, n) for n in names if fnmatch.fnmatch(n, name))

    @functools.cached_property
    def _home(self) -> str:
        return self.sftp.normalize(".")

    def key(self, path: str) -> str:
        """The path to compare locations by: in full (a path not starting with / is in the login folder)."""
        return posixpath.normpath(posixpath.join(self._home, path))

    def is_dir(self, path: str) -> bool:
        st = self._stat(path)
        return bool(st and stat.S_ISDIR(st.st_mode))

    def exists(self, path: str) -> bool:
        return self._stat(path) is not None

    def walk(self, root: str):
        """(path, path relative to root, is a folder) for every folder and file under root."""
        for entry in self.sftp.listdir_attr(root):
            full = posixpath.join(root, entry.filename)
            is_folder = stat.S_ISDIR(entry.st_mode)
            yield full, entry.filename, is_folder
            if is_folder:
                for sub, rel, sub_is_folder in self.walk(full):
                    yield sub, posixpath.join(entry.filename, rel), sub_is_folder

    def makedirs(self, folder: str):
        if not folder or self.is_dir(folder):
            return
        self.makedirs(posixpath.dirname(folder))
        self.sftp.mkdir(folder)

    def open(self, path: str, mode: str):
        if "w" in mode:
            self.makedirs(posixpath.dirname(path))
        return self.sftp.open(path, mode)

    def remove(self, path: str):
        if stat.S_ISDIR(self.sftp.lstat(path).st_mode):  # a link goes, never what it points to
            for entry in self.sftp.listdir_attr(path):
                self.remove(posixpath.join(path, entry.filename))
            self.sftp.rmdir(path)
        else:
            self.sftp.remove(path)

    def close(self):
        self.sftp.close()
        self.client.close()


class _Path(fields.Text):
    """A path, on this computer or on the server chosen in the setting labelled `server`."""

    def __init__(self, server: str, **kw):
        super().__init__(**kw)
        self.server = server

    def coerce(self, value):
        value = str(value)
        name, sep, rest = value.partition(":")
        # The form connection:path is gone; never read it as a path on this computer (C: is a Windows drive).
        if sep and len(name) > 1 and IDENTIFIER.match(name):
            raise ValueError(f"'{value}' names the server before the path, which is no longer read: choose {name} "
                             f"in '{self.server}' and write the path alone, {rest}")
        return value


def _open_location(ctx, server: str | None, stack: ExitStack):
    """(filesystem, where) for a path on this computer (no server) or on the server of an SSH connection;
    `where` is "local" or the connection name."""
    if not server:
        return LocalFiles(), "local"
    fs = ServerFiles(ssh.connect(ctx.connection(server), ctx.home))
    stack.callback(fs.close)
    return fs, server


def _shown(path: str, server: str | None) -> str:
    return f"{path} on {server}" if server else path


def _same_file(where: str, fs, a: str, b: str) -> bool:
    if where == "local":
        if os.path.exists(a) and os.path.exists(b):
            return os.path.samefile(a, b)
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
    return fs.key(a) == fs.key(b)


def _within(fs, path: str, folder: str) -> bool:
    """True when path is folder or inside it; names that differ only in case may be one folder, so count as one."""
    return fs.sep_join(fs.key(path), "").casefold().startswith(fs.sep_join(fs.key(folder), "").casefold())


def _copy_file(src_fs, src, dst_fs, dst, ctx):
    with src_fs.open(src, "rb") as fin, dst_fs.open(dst, "wb") as fout:
        while chunk := fin.read(CHUNK):
            ctx.check_cancelled()
            fout.write(chunk)


def _copy(ctx, config, move: bool) -> list[str]:
    source, destination = config.source, config.destination
    with ExitStack() as stack:
        src_fs, src_where = _open_location(ctx, config.source_server, stack)
        dst_fs, dst_where = _open_location(ctx, config.destination_server, stack)
        dst = dst_fs.norm(destination)
        matches = src_fs.expand(source)
        if not matches:
            raise FileNotFoundError(f"nothing found at {_shown(source, config.source_server)}")
        into_folder = (len(matches) > 1 or src_fs.is_dir(matches[0]) or destination.endswith(("/", "\\"))
                       or dst_fs.is_dir(dst))
        folders, pairs = [], []  # every folder is made, so empty ones are kept too
        for match in matches:
            base = os.path.basename(match.rstrip("/\\"))
            target = dst_fs.sep_join(dst, base) if into_folder else dst
            if src_fs.is_dir(match):
                # A move deletes its sources once copied, so it would delete a copy made inside one.
                if move and src_where == dst_where and _within(dst_fs, target, match):
                    raise ValueError(f"cannot move a folder into itself: {match}")
                folders.append(target)
                for found, rel, is_folder in src_fs.walk(match):
                    to = dst_fs.sep_join(target, *rel.replace("\\", "/").split("/"))
                    if is_folder:
                        folders.append(to)
                    else:
                        pairs.append((found, to))
            else:
                pairs.append((match, target))
        if src_where == dst_where:
            same = [t for f, t in pairs if _same_file(src_where, dst_fs, f, t)]
            if same:  # writing a file onto itself would empty it (and a move would then delete it)
                raise ValueError(f"cannot copy a file onto itself: {', '.join(same[:5])}")
        # Names that differ only in case are one file on Windows, macOS and many servers and network folders.
        keys = [dst_fs.key(t).casefold() for _, t in pairs]
        counts = Counter(keys)
        clashes = sorted({t for (_, t), k in zip(pairs, keys) if counts[k] > 1})
        if clashes:  # the last would replace the others (and a move would then delete them all)
            raise ValueError("several files would be copied to the same place (names that differ only in case "
                             f"count as one): {', '.join(clashes[:5])}")
        if not config.overwrite:
            existing = [t for _, t in pairs if dst_fs.exists(t)]
            if existing:
                raise FileExistsError(f"already exists (turn on Overwrite to replace it): {', '.join(existing[:5])}")
        for folder in folders:
            dst_fs.makedirs(folder)
        for src_file, target in pairs:
            _copy_file(src_fs, src_file, dst_fs, target, ctx)
        ctx.log(f"{'moved' if move else 'copied'} {len(pairs)} file(s) to "
                f"{_shown(destination, config.destination_server)}")
        if move:
            for match in matches:
                src_fs.remove(match)
        return [t for _, t in pairs]


WHERE = ("This computer (its folders and network folders, e.g. \\\\fileserver\\share), or the server of one of "
         "your SSH connections (Tools > Connections)")
FILES = ("a file (C:/data/sales.csv, ~/outbox/sales.csv), a folder with everything in it (C:/data/reports), or a "
         "pattern in the last part (C:/data/*.csv, ~/logs/app-*.log)")


class CopyFiles(Block):
    type_id = "files.copy"
    title = "Copy Files"
    description = ("Copies a file, a folder with everything in it, or the files matching a pattern. For the source "
                   "and the destination, choose where each is: this computer (also network folders) or a server.")
    category = "Files"
    inputs = {"after": ports.Any(required=False, any_of=True)}  # just sets the order
    outputs = {"files": ports.Any()}
    config = {
        "source_server": fields.Server(label="Source on", help=f"Where the source is: {WHERE}"),
        "source": _Path("Source on", help=f"What to copy or move: {FILES}"),
        "destination_server": fields.Server(label="Destination on", help=f"Where the destination is: {WHERE}"),
        "destination": _Path("Destination on", help=(
            "A folder, ending with / (C:/archive/, ~/incoming/): what is copied or moved goes into it, a folder "
            "with its name. Or, for one file, its new name (C:/archive/sales-old.csv). Missing folders are made.")),
        "overwrite": fields.Bool(default=True, help=(
            "On: a file already at the destination is replaced. Off: if any file is already there, nothing is "
            "copied and the block fails.")),
    }

    def run(self, ctx, after=None):
        return {"files": _copy(ctx, self.config, move=False)}


class MoveFiles(CopyFiles):
    type_id = "files.move"
    title = "Move Files"
    description = ("Moves a file, a folder with everything in it, or the files matching a pattern: copies them, then "
                   "deletes them where they were. For the source and the destination, choose where each is: this "
                   "computer (also network folders) or a server.")

    def run(self, ctx, after=None):
        return {"files": _copy(ctx, self.config, move=True)}


class DeleteFiles(Block):
    type_id = "files.delete"
    title = "Delete Files"
    description = ("Deletes a file, a folder with everything in it, or the files matching a pattern, on this "
                   "computer (also network folders) or on a server. Nothing there: nothing is deleted.")
    category = "Files"
    inputs = {"after": ports.Any(required=False, any_of=True)}  # just sets the order
    outputs = {"deleted": ports.Any()}
    config = {
        "server": fields.Server(label="Path on", help=f"Where the path is: {WHERE}"),
        "path": _Path("Path on", help=f"What to delete: {FILES}. A link is deleted, never what it points to."),
    }

    def run(self, ctx, after=None):
        with ExitStack() as stack:
            fs, _ = _open_location(ctx, self.config.server, stack)
            matches = fs.expand(self.config.path)
            for match in matches:
                fs.remove(match)
        ctx.log(f"deleted {len(matches)} item(s)")
        return {"deleted": matches}

