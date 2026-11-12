r"""The apply journal: what makes every apply reversible, also after a crash.

One JSON object per line, appended, flushed and fsynced before the action it describes
happens. While an apply runs (or after it crashed) the journal is the file
``<journals>/<repo id>/apply.journal.jsonl``; when the apply is finished or rolled back
the file moves to ``<journals>/<repo id>/archive/<time>-<outcome>.jsonl``. So "an open
journal exists" is exactly "that file exists" (and has no ``closed`` record).

Records (every record also has ``t``, the UTC time):

* ``{"type": "begin", "format": 1, "repo_root", "original_branch" (null = detached HEAD),
  "original_commit", "tool_version", "pid"}``
* ``{"type": "branch", "name"}``: written before the branch is created
* ``{"type": "step", "name", "state": "started" | "finished" | "failed"}``
* ``{"type": "path", "rel_path", "action", "step", "tracked", "existed", "new_dirs",
  "backup"}``: written BEFORE the path is touched. ``tracked``: the file is in the
  original commit (restored with git). ``new_dirs``: folders that did not exist yet.
  ``backup``: copy of a file that existed but is not in the original commit.
* ``{"type": "scope", "rel_path", "step", "untracked_before"}``: a folder or file a
  DaVinci tool may write, recorded before the tool starts.
* ``{"type": "commit", "sha"}``: the commit was created
* ``{"type": "note", ...}``: free information (rollback progress)
* ``{"type": "closed", "outcome": "committed" | "rolled_back" | "empty"}``
"""

from __future__ import annotations

import json
import os
import shutil
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

from porttool.core.errors import RepoStateError

JOURNAL_NAME = "apply.journal.jsonl"
ARCHIVE_DIR = "archive"
BACKUP_DIR = "backup"
WORK_DIR = "work"
FORMAT = 1


@dataclass(frozen=True, slots=True)
class PathRecord:
    rel_path: str
    action: str
    step: str
    tracked: bool
    existed: bool
    new_dirs: tuple[str, ...] = ()
    backup: str | None = None


@dataclass(frozen=True, slots=True)
class ScopeRecord:
    rel_path: str
    step: str
    untracked_before: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class JournalState:
    """Everything a rollback needs, read back from the journal file alone."""

    file: Path
    repo_root: str
    original_branch: str | None
    original_commit: str
    branch: str | None
    paths: tuple[PathRecord, ...]
    scopes: tuple[ScopeRecord, ...]
    steps: tuple[tuple[str, str], ...]
    commit: str | None
    closed: str | None

    @property
    def folder(self) -> Path:
        return self.file.parent

    @property
    def backup_dir(self) -> Path:
        return self.file.parent / BACKUP_DIR


def _utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def normalise(repo_root: Path, rel_path: str | os.PathLike[str]) -> str:
    """``rel_path`` as a repo-relative path with forward slashes; refuses paths outside."""
    text = os.fspath(rel_path).replace("\\", "/")
    absolute = Path(os.path.abspath(repo_root / text))
    root = Path(os.path.abspath(repo_root))
    if absolute == root or not absolute.is_relative_to(root):
        raise RepoStateError(
            f"The path {text} is outside the repository {root}: it cannot be journaled, "
            "so it must not be written."
        )
    return absolute.relative_to(root).as_posix()


def _key(rel_path: str) -> str:
    return os.path.normcase(rel_path)


class ApplyJournal:
    """Writer side of the journal. Implements ``porttool.core.interfaces.Journal``.

    Thread-safe: the ARXML writer records paths from worker threads.
    """

    def __init__(
        self, file: Path, repo_root: Path, tracked: Iterable[str], stream: IO[bytes]
    ) -> None:
        self.file = file
        self.repo_root = Path(os.path.abspath(repo_root))
        self.current_step = ""
        self._tracked = {_key(path) for path in tracked}
        self._stream: IO[bytes] | None = stream
        self._lock = threading.Lock()
        self._paths: dict[str, PathRecord] = {}
        self._scopes: dict[str, ScopeRecord] = {}
        self._backups = 0

    @classmethod
    def create(
        cls,
        folder: Path,
        *,
        repo_root: Path,
        original_branch: str | None,
        original_commit: str,
        tracked: Iterable[str],
        tool_version: str,
    ) -> ApplyJournal:
        """Start a journal. Refuses when one is already open in ``folder``."""
        try:
            folder.mkdir(parents=True, exist_ok=True)
            stream = open(folder / JOURNAL_NAME, "xb")  # noqa: SIM115 - kept open on purpose
        except FileExistsError:
            raise RepoStateError(
                f"A journal of an unfinished apply exists: {folder / JOURNAL_NAME}. "
                "Roll it back first."
            ) from None
        except OSError as error:
            raise RepoStateError(
                f"The journal {folder / JOURNAL_NAME} cannot be created: {error.strerror or error}",
                detail=repr(error),
            ) from error
        journal = cls(folder / JOURNAL_NAME, repo_root, tracked, stream)
        shutil.rmtree(journal.backup_dir, ignore_errors=True)  # left by an archived journal
        journal._write(
            {
                "type": "begin",
                "format": FORMAT,
                "repo_root": str(journal.repo_root),
                "original_branch": original_branch,
                "original_commit": original_commit,
                "tool_version": tool_version,
                "pid": os.getpid(),
            }
        )
        return journal

    @property
    def backup_dir(self) -> Path:
        return self.file.parent / BACKUP_DIR

    @property
    def work_dir(self) -> Path:
        """Scratch folder outside the repository (delta ARXML, script task files)."""
        folder = self.file.parent / WORK_DIR
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    # ---- writing

    def _write(self, record: dict[str, object]) -> None:
        if self._stream is None:
            raise RepoStateError(f"The journal {self.file} is already closed.")
        line = json.dumps({"t": _utc(), **record}, ensure_ascii=False) + "\n"
        self._stream.write(line.encode("utf-8"))
        self._stream.flush()
        os.fsync(self._stream.fileno())

    def record(self, rel_path: str, action: str) -> None:
        """Journal a path BEFORE it is created, changed or removed. Idempotent."""
        norm = normalise(self.repo_root, rel_path)
        key = _key(norm)
        with self._lock:
            if key in self._paths:
                return  # the first record describes the state before this run
            absolute = self.repo_root / norm
            existed = os.path.lexists(absolute)
            tracked = key in self._tracked
            new_dirs: list[str] = []
            parent = absolute.parent
            while parent != self.repo_root and not parent.exists():
                new_dirs.append(parent.relative_to(self.repo_root).as_posix())
                parent = parent.parent
            backup: str | None = None
            if existed and not tracked and absolute.is_file():
                self.backup_dir.mkdir(parents=True, exist_ok=True)
                self._backups += 1
                backup = f"{self._backups:05d}"
                with open(self.backup_dir / backup, "wb") as stream:
                    stream.write(absolute.read_bytes())
                    stream.flush()
                    os.fsync(stream.fileno())
            item = PathRecord(
                rel_path=norm,
                action=action,
                step=self.current_step,
                tracked=tracked,
                existed=existed,
                new_dirs=tuple(new_dirs),
                backup=backup,
            )
            self._write(
                {
                    "type": "path",
                    "rel_path": norm,
                    "action": action,
                    "step": item.step,
                    "tracked": tracked,
                    "existed": existed,
                    "new_dirs": new_dirs,
                    "backup": backup,
                }
            )
            self._paths[key] = item

    def scope(self, rel_path: str, untracked_before: Iterable[str]) -> None:
        """Journal a folder or file a DaVinci tool may write, before the tool starts.

        ``"."`` is the whole repository folder (when the ARXML root is the repo itself).
        """
        norm = "." if rel_path in (".", "") else normalise(self.repo_root, rel_path)
        with self._lock:
            if _key(norm) in self._scopes:
                return
            item = ScopeRecord(norm, self.current_step, tuple(sorted(untracked_before)))
            self._write(
                {
                    "type": "scope",
                    "rel_path": norm,
                    "step": item.step,
                    "untracked_before": list(item.untracked_before),
                }
            )
            self._scopes[_key(norm)] = item

    def step(self, name: str, state: str) -> None:
        with self._lock:
            self._write({"type": "step", "name": name, "state": state})

    def note(self, kind: str, **fields: object) -> None:
        with self._lock:
            self._write({"type": kind, **fields})

    # ---- reading what was recorded so far

    def paths(self, step: str | None = None) -> tuple[PathRecord, ...]:
        with self._lock:
            return tuple(p for p in self._paths.values() if step is None or p.step == step)

    def scopes(self) -> tuple[ScopeRecord, ...]:
        with self._lock:
            return tuple(self._scopes.values())

    def has(self, rel_path: str) -> bool:
        with self._lock:
            return _key(rel_path.replace("\\", "/")) in self._paths

    def covers(self, rel_path: str) -> bool:
        """Is this path journaled, directly or through a tool scope?"""
        key = _key(rel_path.replace("\\", "/"))
        with self._lock:
            if key in self._paths:
                return True
            return any(
                scope == "." or key == scope or key.startswith(scope + "/")
                for scope in self._scopes
            )

    # ---- closing

    def release(self) -> None:
        """Close the file handle. The journal stays open (it has no ``closed`` record)."""
        with self._lock:
            if self._stream is not None:
                self._stream.close()
                self._stream = None

    def finish(self, outcome: str) -> Path:
        """Write the ``closed`` record and move the journal to the archive."""
        with self._lock:
            self._write({"type": "closed", "outcome": outcome})
        self.release()
        return archive(self.file, outcome)


def archive(file: Path, outcome: str) -> Path:
    """Move a journal file to the archive folder and drop its backups."""
    folder = file.parent / ARCHIVE_DIR
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = folder / f"{stamp}-{outcome}.jsonl"
    number = 1
    while target.exists():
        number += 1
        target = folder / f"{stamp}-{outcome}-{number}.jsonl"
    os.replace(file, target)
    shutil.rmtree(file.parent / BACKUP_DIR, ignore_errors=True)
    return target


def append_record(file: Path, record: dict[str, object]) -> None:
    """Append one record to a journal that this process did not write (recovery)."""
    with open(file, "ab") as stream:
        line = json.dumps({"t": _utc(), **record}, ensure_ascii=False) + "\n"
        stream.write(line.encode("utf-8"))
        stream.flush()
        os.fsync(stream.fileno())


def read_journal(file: Path) -> JournalState | None:
    """Read a journal back. None when it holds no ``begin`` record (nothing was touched).

    A torn last line (the process died while writing it) is ignored: the action that
    line announced had not started.
    """
    try:
        raw = file.read_bytes()
    except OSError as error:
        raise RepoStateError(
            f"The journal {file} cannot be read: {error.strerror or error}", detail=repr(error)
        ) from error
    records: list[dict[str, object]] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            records.append(item)
    begin = next((r for r in records if r.get("type") == "begin"), None)
    if begin is None or not isinstance(begin.get("original_commit"), str):
        return None
    paths: dict[str, PathRecord] = {}
    scopes: dict[str, ScopeRecord] = {}
    steps: list[tuple[str, str]] = []
    branch: str | None = None
    commit: str | None = None
    closed: str | None = None
    for item in records:
        kind = item.get("type")
        if kind == "branch":
            branch = str(item.get("name"))
        elif kind == "step":
            steps.append((str(item.get("name")), str(item.get("state"))))
        elif kind == "path":
            rel = str(item.get("rel_path"))
            new_dirs = item.get("new_dirs")
            backup = item.get("backup")
            paths.setdefault(
                _key(rel),
                PathRecord(
                    rel_path=rel,
                    action=str(item.get("action", "")),
                    step=str(item.get("step", "")),
                    tracked=bool(item.get("tracked")),
                    existed=bool(item.get("existed")),
                    new_dirs=tuple(str(x) for x in new_dirs) if isinstance(new_dirs, list) else (),
                    backup=backup if isinstance(backup, str) else None,
                ),
            )
        elif kind == "scope":
            rel = str(item.get("rel_path"))
            before = item.get("untracked_before")
            scopes.setdefault(
                _key(rel),
                ScopeRecord(
                    rel_path=rel,
                    step=str(item.get("step", "")),
                    untracked_before=tuple(str(x) for x in before)
                    if isinstance(before, list)
                    else (),
                ),
            )
        elif kind == "commit":
            commit = str(item.get("sha"))
        elif kind == "closed":
            closed = str(item.get("outcome"))
    original_branch = begin.get("original_branch")
    return JournalState(
        file=file,
        repo_root=str(begin.get("repo_root", "")),
        original_branch=original_branch if isinstance(original_branch, str) else None,
        original_commit=str(begin["original_commit"]),
        branch=branch,
        paths=tuple(paths.values()),
        scopes=tuple(scopes.values()),
        steps=tuple(steps),
        commit=commit,
        closed=closed,
    )


def open_journal_file(folder: Path) -> Path | None:
    """The journal of an unfinished apply in ``folder``, or None.

    A journal that was closed but not yet archived, or that holds no ``begin`` record,
    describes no unfinished work: it is archived here and None is returned.
    """
    file = folder / JOURNAL_NAME
    if not file.is_file():
        return None
    state = read_journal(file)
    if state is None:
        archive(file, "empty")
        return None
    if state.closed is not None:
        archive(file, state.closed)
        return None
    return file
