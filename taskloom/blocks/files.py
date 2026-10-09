"""File blocks: copy, move and delete between local folders (incl. network shares) and servers.

A location is a local path (C:/data/x.csv, \\\\server\\share\\x.csv, /home/me/x.csv) or
`<ssh connection>:<path>` on a server, e.g. `appserver:~/app/config.yaml`.
A wildcard is allowed in the last part of a source path: `appserver:~/logs/*.log`.
"""

from __future__ import annotations

import fnmatch
import glob
import os
import posixpath
import shutil
import stat
from contextlib import ExitStack

from .. import ssh
from ..block import Block, fields, ports

CHUNK = 1 << 20


class LocalFiles:
    sep_join = staticmethod(os.path.join)

    def expand(self, path: str) -> list[str]:
        path = os.path.expanduser(path)
        return sorted(glob.glob(path)) if glob.has_magic(path) else ([path] if os.path.exists(path) else [])

    def norm(self, path: str) -> str:
        return os.path.expanduser(path)

    def is_dir(self, path: str) -> bool:
        return os.path.isdir(path)

    def exists(self, path: str) -> bool:
        return os.path.exists(path)

    def files_under(self, root: str):
        for folder, _, names in os.walk(root):
            for name in names:
                full = os.path.join(folder, name)
                yield full, os.path.relpath(full, root)

    def open(self, path: str, mode: str):
        if "w" in mode:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        return open(path, mode)

    def remove(self, path: str):
        shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)

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
        folder, name = posixpath.split(path)
        if glob.has_magic(name):
            return sorted(posixpath.join(folder, n) for n in self.sftp.listdir(folder or ".") if fnmatch.fnmatch(n, name))
        return [path] if self._stat(path) else []

    def is_dir(self, path: str) -> bool:
        st = self._stat(path)
        return bool(st and stat.S_ISDIR(st.st_mode))

    def exists(self, path: str) -> bool:
        return self._stat(path) is not None

    def files_under(self, root: str):
        for entry in self.sftp.listdir_attr(root):
            full = posixpath.join(root, entry.filename)
            if stat.S_ISDIR(entry.st_mode):
                for sub, rel in self.files_under(full):
                    yield sub, posixpath.join(entry.filename, rel)
            else:
                yield full, entry.filename

    def _makedirs(self, folder: str):
        if not folder or self.is_dir(folder):
            return
        self._makedirs(posixpath.dirname(folder))
        self.sftp.mkdir(folder)

    def open(self, path: str, mode: str):
        if "w" in mode:
            self._makedirs(posixpath.dirname(path))
        return self.sftp.open(path, mode)

    def remove(self, path: str):
        if self.is_dir(path):
            for entry in self.sftp.listdir_attr(path):
                self.remove(posixpath.join(path, entry.filename))
            self.sftp.rmdir(path)
        else:
            self.sftp.remove(path)

    def close(self):
        self.sftp.close()
        self.client.close()


def _open_location(ctx, location: str, stack: ExitStack):
    """(filesystem, path, where) for a location string; `where` is "local" or the connection name."""
    name, sep, path = location.partition(":")
    if sep and len(name) > 1:  # one letter is a Windows drive, e.g. C:/data
        conns = ctx.home.connections()
        if conns.get(name, {}).get("kind") == "ssh":
            fs = ServerFiles(ssh.connect(ctx.connection(name), ctx.home))
            stack.callback(fs.close)
            return fs, path, name
    return LocalFiles(), location, "local"


def _same_file(where: str, a: str, b: str) -> bool:
    if where == "local":
        if os.path.exists(a) and os.path.exists(b):
            return os.path.samefile(a, b)
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
    return posixpath.normpath(a) == posixpath.normpath(b)


def _copy_file(src_fs, src, dst_fs, dst, ctx):
    with src_fs.open(src, "rb") as fin, dst_fs.open(dst, "wb") as fout:
        while chunk := fin.read(CHUNK):
            ctx.check_cancelled()
            fout.write(chunk)


def _copy(ctx, source: str, destination: str, overwrite: bool, move: bool) -> list[str]:
    with ExitStack() as stack:
        src_fs, src, src_where = _open_location(ctx, source, stack)
        dst_fs, dst, dst_where = _open_location(ctx, destination, stack)
        dst = dst_fs.norm(dst)
        matches = src_fs.expand(src)
        if not matches:
            raise FileNotFoundError(f"nothing found at {source}")
        single_file = len(matches) == 1 and not glob.has_magic(src) and not src_fs.is_dir(matches[0])
        into_folder = not single_file or destination.endswith(("/", "\\")) or dst_fs.is_dir(dst)
        pairs = []
        for match in matches:
            base = os.path.basename(match.rstrip("/\\"))
            target = dst_fs.sep_join(dst, base) if into_folder else dst
            if src_fs.is_dir(match):
                pairs += [(f, dst_fs.sep_join(target, *rel.replace("\\", "/").split("/"))) for f, rel in src_fs.files_under(match)]
            else:
                pairs.append((match, target))
        if src_where == dst_where:
            same = [t for f, t in pairs if _same_file(src_where, f, t)]
            if same:  # writing a file onto itself would empty it (and a move would then delete it)
                raise ValueError(f"cannot copy a file onto itself: {', '.join(same[:5])}")
        if not overwrite:
            existing = [t for _, t in pairs if dst_fs.exists(t)]
            if existing:
                raise FileExistsError(f"already exists (set overwrite to allow): {', '.join(existing[:5])}")
        for src_file, target in pairs:
            _copy_file(src_fs, src_file, dst_fs, target, ctx)
        ctx.log(f"{'moved' if move else 'copied'} {len(pairs)} file(s) to {destination}")
        if move:
            for match in matches:
                src_fs.remove(match)
        return [t for _, t in pairs]


class CopyFiles(Block):
    type_id = "files.copy"
    title = "Copy Files"
    description = 'Copies files or folders: on this computer, to or from a network folder, or to or from a server.'
    category = "Files"
    inputs = {"after": ports.Any(required=False)}
    outputs = {"files": ports.Any()}
    config = {
        "source": fields.Text(help="A file, folder or pattern; server paths as connection:path"),
        "destination": fields.Text(help="A file path, or a folder (end with /)"),
        "overwrite": fields.Bool(default=True),
    }

    def run(self, ctx, after=None):
        return {"files": _copy(ctx, self.config.source, self.config.destination, self.config.overwrite, move=False)}


class MoveFiles(CopyFiles):
    type_id = "files.move"
    title = "Move Files"
    description = 'Moves files or folders: on this computer, to or from a network folder, or to or from a server.'

    def run(self, ctx, after=None):
        return {"files": _copy(ctx, self.config.source, self.config.destination, self.config.overwrite, move=True)}


class DeleteFiles(Block):
    type_id = "files.delete"
    title = "Delete Files"
    description = 'Deletes files or folders, on this computer or on a server.'
    category = "Files"
    inputs = {"after": ports.Any(required=False)}
    outputs = {"deleted": ports.Any()}
    config = {"path": fields.Text(help="A file, folder or pattern; server paths as connection:path")}

    def run(self, ctx, after=None):
        with ExitStack() as stack:
            fs, path, _ = _open_location(ctx, self.config.path, stack)
            matches = fs.expand(path)
            for match in matches:
                fs.remove(match)
        ctx.log(f"deleted {len(matches)} item(s)")
        return {"deleted": matches}

