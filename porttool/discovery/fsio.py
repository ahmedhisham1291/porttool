"""File-system helpers for discovery: long paths, a pruning walk, atomic writes.

Discovery only reads the ECU repo. The single place it writes is its own output folder.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

#: Windows refuses plain paths from 260 characters on (248 for folders) unless the
#: machine opted in to long paths. Above this length we switch to the ``\\?\`` form.
LONG_PATH_THRESHOLD = 240

_DRIVE_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")
_EXTENDED_PREFIX = "\\\\?\\"

MAX_WALK_DEPTH = 64
CHUNK_SIZE = 1 << 20


def io_path(path: str) -> str:
    """Return ``path`` in a form the Windows file API accepts beyond 260 characters.

    ``path`` must be absolute and normalized. Short paths and paths that are not Windows
    drive or UNC paths come back unchanged.
    """
    if len(path) < LONG_PATH_THRESHOLD or path.startswith(_EXTENDED_PREFIX):
        return path
    if _DRIVE_PATH_RE.match(path):
        return _EXTENDED_PREFIX + path.replace("/", "\\")
    if path.startswith("\\\\"):
        return _EXTENDED_PREFIX + "UNC\\" + path[2:].replace("/", "\\")
    return path


def iso_utc(mtime_ns: int) -> str:
    """Format a file time as ``2026-01-31T12:00:00Z``."""
    moment = datetime.fromtimestamp(mtime_ns / 1_000_000_000, tz=UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def now_utc() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- exclusion


def _glob_to_regex(pattern: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "*":
            if pattern[index : index + 3] == "**/":
                out.append("(?:.*/)?")
                index += 3
                continue
            if pattern[index : index + 2] == "**":
                out.append(".*")
                index += 2
                continue
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(char))
        index += 1
    return "".join(out)


class ExcludeMatcher:
    """Glob patterns relative to the repo root, forward slashes, case-insensitive.

    A pattern without ``/`` matches a file or folder name at any depth (``GenData``,
    ``*.bak``). A pattern with ``/`` is anchored at the repo root (``Config/Sip/**``).
    A matching folder is skipped with everything below it.
    """

    def __init__(self, patterns: Sequence[str]) -> None:
        self.patterns: tuple[str, ...] = tuple(p for p in (x.strip() for x in patterns) if p)
        self._name_res: list[re.Pattern[str]] = []
        self._path_res: list[re.Pattern[str]] = []
        for raw in self.patterns:
            pattern = raw.replace("\\", "/").strip("/")
            if pattern.endswith("/**"):
                pattern = pattern[:-3]
            if not pattern:
                continue
            compiled = re.compile("^" + _glob_to_regex(pattern) + "$", re.IGNORECASE)
            (self._path_res if "/" in pattern else self._name_res).append(compiled)

    def matches(self, rel: str) -> bool:
        name = rel.rpartition("/")[2]
        return any(r.match(name) for r in self._name_res) or any(
            r.match(rel) for r in self._path_res
        )


# --------------------------------------------------------------------------- walk


@dataclass(frozen=True, slots=True)
class FileEntry:
    """One file below the repo root. ``rel`` uses forward slashes."""

    rel: str
    path: str
    size: int
    mtime_ns: int

    @property
    def name(self) -> str:
        return self.rel.rpartition("/")[2]

    @property
    def folder(self) -> str:
        return self.rel.rpartition("/")[0]


@dataclass(slots=True)
class WalkResult:
    files: list[FileEntry] = field(default_factory=list)
    dirs: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # excluded folders and files
    errors: list[tuple[str, str]] = field(default_factory=list)  # (rel folder, error type)
    too_deep: list[str] = field(default_factory=list)


def walk_repo(root: str, matcher: ExcludeMatcher) -> WalkResult:
    """List every file below ``root``. Never follows links; never raises for one folder."""
    result = WalkResult()
    pending: list[tuple[str, str, int]] = [(root, "", 0)]
    while pending:
        folder, rel_folder, depth = pending.pop()
        try:
            with os.scandir(io_path(folder)) as scan:
                entries = sorted(scan, key=lambda entry: entry.name)
        except OSError as error:
            result.errors.append((rel_folder, type(error).__name__))
            continue
        for entry in entries:
            rel = f"{rel_folder}/{entry.name}" if rel_folder else entry.name
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
                is_file = entry.is_file(follow_symlinks=False)
            except OSError as error:
                result.errors.append((rel, type(error).__name__))
                continue
            if not is_dir and not is_file:
                continue  # link or special file: never followed
            if matcher.matches(rel):
                result.skipped.append(rel)
                continue
            path = os.path.join(folder, entry.name)
            if is_dir:
                result.dirs.append(rel)
                if depth + 1 >= MAX_WALK_DEPTH:
                    result.too_deep.append(rel)
                else:
                    pending.append((path, rel, depth + 1))
                continue
            try:
                stat = entry.stat(follow_symlinks=False)
            except OSError as error:
                result.errors.append((rel, type(error).__name__))
                continue
            result.files.append(FileEntry(rel, path, stat.st_size, stat.st_mtime_ns))
    result.files.sort(key=lambda item: item.rel)
    result.dirs.sort()
    return result


def read_chunks(path: str, chunk_size: int = CHUNK_SIZE) -> Iterator[bytes]:
    """Yield the content of a file in chunks. Raises OSError if it cannot be read."""
    with open(io_path(path), "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                return
            yield chunk


# --------------------------------------------------------------------------- writing


def atomic_write_bytes(path: str, data: bytes, *, retries: int = 3, backoff_s: float = 0.2) -> None:
    """Write ``data`` to ``path`` through a temporary file in the same folder.

    A sharing violation (``PermissionError`` from ``os.replace``) is retried ``retries``
    times with a growing pause. Raises OSError when the file cannot be written.
    """
    folder, name = os.path.split(path)
    temporary = os.path.join(folder, f".{name}.{os.getpid()}.tmp")
    with open(io_path(temporary), "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    for attempt in range(retries + 1):
        try:
            os.replace(io_path(temporary), io_path(path))
            return
        except PermissionError:
            if attempt == retries:
                remove_quietly(temporary)
                raise
            time.sleep(backoff_s * (2**attempt))


def remove_quietly(path: str) -> bool:
    """Delete a file of ours; report (do not raise) when that is not possible."""
    try:
        os.remove(io_path(path))
    except OSError:
        return False
    return True


def atomic_write_text(path: str, text: str, *, newline: str = "\n") -> None:
    """Write UTF-8 text atomically with the given line ending."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if newline != "\n":
        normalized = normalized.replace("\n", newline)
    atomic_write_bytes(path, normalized.encode("utf-8"))
