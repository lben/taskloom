"""Git in the editor: File → Save & Commit… commits a flow file, and only it, to the Git repository of its folder
(creating one there if there is none); Save as… gives the copy the original's Git history.

Taskloom does this itself with dulwich, so no git program is needed, and none is ever started. dulwich is imported
only inside these functions, so the runner never loads it. Only its lower-level parts are used (objects, trees, the
index, refs): its porcelain commits the whole index, makes empty commits, invents a name and email, concludes
merges, runs hooks and skips ignored files silently. So Taskloom's commits run no hooks and are never signed, the
committed bytes are the file's as written (no .gitattributes, filters, LFS or line-end conversion), and Taskloom
never writes any Git config."""

from __future__ import annotations

import datetime as dt
import os
import time
from dataclasses import dataclass
from pathlib import Path

# In the git dir while an operation is going (a commit there would conclude or break it).
OPERATIONS = (("MERGE_HEAD", "merge"), ("CHERRY_PICK_HEAD", "cherry-pick"), ("REVERT_HEAD", "revert"),
              ("rebase-merge", "rebase"), ("rebase-apply", "rebase"))


class GitError(Exception):
    """Why Taskloom did not commit (the text the editor shows)."""


@dataclass
class Target:
    """Where a file is committed: the repository's root (the folder one will be created in, if `new`), the branch,
    and the file's path in the repository (with / separators)."""
    root: Path
    branch: str
    new: bool
    path: str


def commit_target(file: Path) -> Target:
    """Where `file` (a resolved path; it need not exist yet) is committed: the repository of its folder, or a new one
    there. Raises GitError, having written nothing, when Taskloom does not commit there."""
    from dulwich.errors import NotGitRepository
    from dulwich.ignore import IgnoreFilterManager
    from dulwich.repo import Repo

    folder = file.parent
    try:
        repo = Repo.discover(str(folder))
    except NotGitRepository:
        if folder == Path.home().resolve() or folder == folder.parent:  # (a drive or share root is its own parent)
            raise GitError(f"Taskloom does not create a Git repository in {folder}: every folder in it would be in "
                           "that repository. Keep the flow in a folder of its own") from None
        return Target(folder, _default_branch(), True, file.name)
    except Exception as e:  # e.g. an unsupported repository format
        root = next((p for p in (folder, *folder.parents) if (p / ".git").exists()), folder)
        raise GitError(f"Cannot use the Git repository in {root}: {e}") from None
    with repo:
        root = Path(repo.path).resolve()
        try:
            path = file.relative_to(root).as_posix()
            if _split_index(repo):
                raise ValueError("its index is split (git update-index --split-index), which Taskloom cannot write")
            index = repo.open_index()
            refs, head = repo.refs.follow(b"HEAD")
            tracked = path.encode() in index or head is not None and _lookup(repo, repo[head].tree, path.encode())
            ignored = not tracked and IgnoreFilterManager.from_repo(repo).is_ignored(path) is True
        except Exception as e:
            raise GitError(f"Cannot use the Git repository in {root}: {e}") from None
        if not refs[-1].startswith(b"refs/heads/"):
            raise GitError(f"The Git repository in {root} has no branch checked out (detached HEAD)")
        for name, operation in OPERATIONS:
            if os.path.exists(os.path.join(repo.controldir(), name)):
                raise GitError(f"The Git repository in {root} is in the middle of a {operation}")
        if ignored:
            raise GitError(f"{file.name} is ignored by Git in {root} (a .gitignore or exclude rule matches it)")
        return Target(root, refs[-1][len(b"refs/heads/"):].decode("utf-8", "replace"), False, path)


def identity(folder: Path, name: str = "", email: str = "") -> tuple[str, str]:
    """The name and email to commit with in the repository of `folder` (or a new one there): `name` and `email` if
    given (the last typed in Taskloom), else user.name and user.email of its Git config (the repository's own, the
    global and the system config); "" for what neither gives."""
    from dulwich.config import StackedConfig
    from dulwich.repo import Repo

    if name and email:
        return name, email
    try:
        with Repo.discover(str(folder)) as repo:
            config = repo.get_config_stack()
    except Exception:  # none there (or one that cannot be read: commit_target says so)
        config = StackedConfig.default()

    def configured(key: str) -> str:
        try:
            return config.get(("user",), key).decode("utf-8", "replace")
        except KeyError:
            return ""

    return name or configured("name"), email or configured("email")


def commit_flow(file: Path, name: str, email: str, message: str) -> str:
    """Save & Commit: commit the saved flow file `file`, and only it, to the repository of its folder (created there
    if there is none), with `message` tidied. The status bar's text; raises with the reason it was not committed.
    Nothing new (the file is as in the last commit): no commit."""
    from dulwich.object_store import commit_tree_changes
    from dulwich.objects import Blob, Tree
    from dulwich.repo import Repo

    target = commit_target(file)  # again: the repository may have changed meanwhile
    path = target.path.encode()
    with (Repo.init(str(target.root)) if target.new else Repo(str(target.root))) as repo:
        blob = Blob.from_string(file.read_bytes())
        refs, head = repo.refs.follow(b"HEAD")
        tree = repo[head].tree if head else None
        found = _lookup(repo, tree, path) if tree else None
        mode = found[0] if found else 0o100644
        if found and found[1] == blob.id:
            _stage(repo, path, blob, mode, file)
            return f"Saved {file.name}. Nothing new to commit: it is the same as in its last commit."
        repo.object_store.add_object(blob)
        who, (now, zone) = f"{name} <{email}>".encode(), _now()
        tree = commit_tree_changes(repo.object_store, tree or Tree(), [(path, mode, blob.id)])
        new = _commit(repo, tree, [head] if head else [], _tidy(message).encode(), who, now, zone)
        _advance(repo, refs[-1], head, new, path, blob, mode, file)
    created = f"Created a Git repository in {target.root}. " if target.new else ""
    return f"{created}Saved and committed {file.name} to {target.root} ({target.branch}, {new.decode()[:7]})."


def carry_history(original: Path, copy: Path, name: str = "", email: str = "") -> str | None:
    """Save as: give `copy` (just written) the Git history of `original` up to its last commit, on the current branch
    of the copy's repository (created in its folder if there is none): in the same repository one commit that copies
    it, in another one each version again. `name` and `email`: the last typed in Taskloom. The status bar's text, or
    None when there is nothing to carry (nothing is done); raises with the reason the history was not copied."""
    from dulwich.object_store import commit_tree_changes
    from dulwich.objects import Tree
    from dulwich.repo import Repo

    found = _versions(original)
    if found is None:
        return None
    versions, source_root, source_path = found
    name, email = identity(copy.parent, name, email)
    if not (name and email):
        raise GitError("Taskloom does not know the name and email to commit with yet: give them once in File → "
                       "Save & Commit…, then Save as… again")
    target = commit_target(copy)
    path = target.path.encode()
    who, (now, zone) = f"{name} <{email}>".encode(), _now()
    newest, mode, blob = versions[0]
    with (Repo.init(str(target.root)) if target.new else Repo(str(target.root))) as repo:
        refs, head = repo.refs.follow(b"HEAD")
        tree, parents = (repo[head].tree, [head]) if head else (Tree(), [])
        if head and _lookup(repo, tree, path):  # Save as over a tracked file: its removal first
            tree = commit_tree_changes(repo.object_store, tree, [(path, None, None)])
            parents = [_commit(repo, tree, parents, f"Remove {target.path}: Save as of {source_path} replaces it\n"
                               .encode(), who, now, zone)]
        if target.root == source_root:  # (a replay here would show every version twice in git log --follow)
            tree = commit_tree_changes(repo.object_store, tree, [(path, mode, blob.id)])
            parents = [_commit(repo, tree, parents, f"Copy {source_path} to {target.path} (Save as)\n\nFrom "
                               f"{source_path} at {newest.id.decode()}.\n".encode(), who, now, zone)]
        else:
            for version, version_mode, version_blob in reversed(versions):  # oldest first
                repo.object_store.add_object(version_blob)
                tree = commit_tree_changes(repo.object_store, tree, [(path, version_mode, version_blob.id)])
                parents = [_commit(repo, tree, parents, version.message, who, now, zone, author=version)]
        _advance(repo, refs[-1], head, parents[0], path, blob, mode, copy)
    n = len(versions)
    said = f"Saved {copy.name} with the Git history of {original.name} ({n} commit{'s' if n != 1 else ''})."
    if target.new:
        said += f" Created a Git repository in {target.root}."
    if copy.read_bytes() != blob.data:
        said += f" Changes since {original.name}'s last commit are not committed yet."
    return said


def _tidy(message: str) -> str:
    """A commit message as typed, without spaces at the ends of its lines and blank lines before and after it."""
    lines = [line.rstrip() for line in message.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + "\n"


def _versions(original: Path) -> tuple[list, Path, str] | None:
    """The original's history up to its last commit: its versions, newest first, each (commit, mode, blob), with its
    repository's root and its path there. Walking from HEAD along first parents, a commit is a version when it
    changed the file; at the commit that added it, the walk goes on from the one path of the parent holding the
    same content (an exact copy or rename, e.g. Save as), if there is one. A version with the same content as the
    one before is dropped (a copy is not a version of its own). None: nothing to carry (no repository, the file is
    not in HEAD, or the repository cannot be read)."""
    from dulwich.object_store import iter_tree_contents
    from dulwich.repo import Repo

    try:
        repo = Repo.discover(str(original.parent))
    except Exception:
        return None
    with repo:
        try:
            root = Path(repo.path).resolve()
            path = original.relative_to(root).as_posix()
            commit, at, found = repo[repo.head()], path.encode(), []
            if _lookup(repo, commit.tree, at) is None:
                return None
            while True:
                mode, sha = _lookup(repo, commit.tree, at)
                parent = repo[commit.parents[0]] if commit.parents else None
                before = _lookup(repo, parent.tree, at) if parent else None
                if before is None or before[1] != sha:
                    found.append((commit, mode, sha))
                if parent is None:
                    break
                if before is None:  # added here
                    same = [e.path for e in iter_tree_contents(repo.object_store, parent.tree) if e.sha == sha]
                    if len(same) != 1:
                        break
                    at = same[0]
                commit = parent
            versions = []
            for commit, mode, sha in reversed(found):
                if not versions or versions[-1][2].id != sha:
                    versions.append((commit, mode, repo[sha]))
            return versions[::-1], root, path
        except Exception:
            return None


def _default_branch() -> str:
    """A new repository's branch: init.defaultBranch of Git config, else master (as dulwich's Repo.init chooses)."""
    from dulwich.config import StackedConfig

    try:
        return StackedConfig.default().get(("init",), "defaultBranch").decode("utf-8", "replace")
    except KeyError:
        return "master"


def _split_index(repo) -> bool:
    """Whether the index is split (git update-index --split-index): most of its entries are then in another file,
    which dulwich does not read, so writing the index would lose them."""
    from dulwich.index import read_index_dict_with_version

    try:
        f = open(repo.index_path(), "rb")
    except FileNotFoundError:
        return False
    with f:
        read_index_dict_with_version(f, repo.object_format)  # it stops before an extension it does not read
        rest = os.fstat(f.fileno()).st_size - f.tell()
        return rest > repo.object_format.oid_length and f.read(4) == b"link"


def _lookup(repo, tree, path: bytes) -> tuple[int, bytes] | None:
    """(mode, id) of `path` in the tree `tree`, or None."""
    from dulwich.errors import NotTreeError
    from dulwich.object_store import tree_lookup_path

    try:
        return tree_lookup_path(repo.__getitem__, tree, path)
    except (KeyError, NotTreeError):
        return None


def _now() -> tuple[int, int]:
    """Now (seconds) and the computer's offset from UTC then (seconds)."""
    now = int(time.time())
    return now, int(dt.datetime.fromtimestamp(now).astimezone().utcoffset().total_seconds())


def _commit(repo, tree, parents, message: bytes, who: bytes, now: int, zone: int, author=None) -> bytes:
    """Store a commit, by `who` now; with `author` (a commit), its author line kept as it is. Its id."""
    from dulwich.objects import Commit

    commit = Commit()
    commit.tree, commit.parents, commit.message = tree, parents, message
    commit.committer, commit.commit_time, commit.commit_timezone = who, now, zone
    if author is None:
        commit.author, commit.author_time, commit.author_timezone = who, now, zone
    else:
        commit.author, commit.author_time = author.author, author.author_time
        commit.author_timezone = author.author_timezone
        commit._author_timezone_neg_utc = author._author_timezone_neg_utc  # a -0000 time zone stays -0000
        commit.encoding = author.encoding  # (the message's)
    repo.object_store.add_object(commit)
    return commit.id


def _stage(repo, path: bytes, blob, mode: int, file: Path):
    """Set the index entry of `path`, and only it, to `blob`: with the file's stat when the file holds it, else with
    none (git then reads the file to compare). The index and the entry it had."""
    from dulwich.file import FileLocked
    from dulwich.index import IndexEntry, index_entry_from_stat

    index = repo.open_index()
    previous = index[path] if path in index else None
    stat = file.stat()
    index[path] = (index_entry_from_stat(stat, blob.id, mode) if file.read_bytes() == blob.data else
                   IndexEntry(ctime=0, mtime=0, dev=0, ino=0, mode=mode, uid=0, gid=0, size=0, sha=blob.id))
    try:
        index.write()  # through index.lock
    except FileLocked:
        raise GitError(f"another program is using this Git repository ({Path(repo.controldir()) / 'index.lock'} "
                       "exists); close it or wait") from None
    return index, previous


def _advance(repo, ref: bytes, old: bytes | None, new: bytes, path: bytes, blob, mode: int, file: Path):
    """Stage `blob` at `path` (see _stage), then move the branch `ref` from `old` (None: no commit yet) to `new`, by
    compare-and-swap. The index is written first, so a held index.lock changes nothing; if the branch moved
    meanwhile (or another program holds its lock), the index entry is put back."""
    from dulwich.file import FileLocked

    index, previous = _stage(repo, path, blob, mode, file)
    commit = repo[new]
    log = {"committer": commit.committer, "timestamp": commit.commit_time, "timezone": commit.commit_timezone,
           "message": (b"commit: " if old else b"commit (initial): ") + commit.message.split(b"\n")[0]}
    try:
        moved = repo.refs.set_if_equals(ref, old, new, **log) if old else repo.refs.add_if_new(ref, new, **log)
    except FileLocked:
        moved = False
    if not moved:
        if previous is None:
            del index[path]
        else:
            index[path] = previous
        index.write()
        raise GitError("the repository changed while committing")
