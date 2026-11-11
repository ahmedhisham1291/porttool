"""Record cache: the extracted records of every file, so a warm load parses nothing.

One SQLite file per repository in ``config.cache_dir``. One row per source file, keyed by
its repo-relative path and valid only for the exact ``(size, mtime_ns)`` it was built
from. The whole cache is versioned by the cache format number, the tool version, the
Python version and the extraction options; a version change empties it.

Why SQLite and not one file per entry: a cold load of 2,000 files would otherwise create
2,000 small files (slow on Windows, where each create is scanned), and a warm load would
open 2,000. Here both are one file and one transaction, and SQLite's journal gives the
atomic replace that PortTool requires of every file write.

Safety: a blob is a pickle of builtin tuples only, and it is read with an unpickler that
refuses every global, so a tampered cache cannot run code. Each blob carries a CRC.

Nothing here is fatal. A broken entry is dropped and rebuilt, a broken cache file is
deleted and rebuilt, and a cache that cannot be used at all raises ``CacheUnavailable``,
which the reader turns into a finding before it continues without cache.
"""

from __future__ import annotations

import hashlib
import io
import os
import pickle
import re
import sqlite3
import sys
import zlib
from collections.abc import Callable, Iterable, Mapping
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

import porttool
from porttool.core.results import Finding, Severity
from porttool.reader import codes
from porttool.reader.records import CACHE_FORMAT, FIELD_COUNT, FileRecords

DB_NAME = "records.sqlite3"

_SQLITE_CORRUPT = 11
_SQLITE_NOTADB = 26
_BUSY_TIMEOUT_S = 5.0
_FIRST_LIST_FIELD = FileRecords._fields.index("findings")


class CacheUnavailable(Exception):
    """The cache cannot be used for this load. Never escapes the reader."""


@dataclass(frozen=True, slots=True)
class CacheRead:
    hits: dict[str, FileRecords]
    obsolete: tuple[str, ...]  # rows of files that are gone or whose entry was corrupt
    findings: tuple[Finding, ...]


# --------------------------------------------------------------------------- location


def repo_id(repo_root: Path) -> str:
    """Folder name of one repository's cache: readable name plus a hash of the full path."""
    absolute = os.path.normcase(os.path.abspath(repo_root))
    digest = hashlib.sha1(absolute.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
    name = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(absolute))[:40] or "repo"
    return f"{name}-{digest}"


def default_cache_dir(repo_root: Path) -> Path | None:
    """``%LOCALAPPDATA%\\PortTool\\cache\\<repo id>``, or None if LOCALAPPDATA is not set."""
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return None
    return Path(base) / "PortTool" / "cache" / repo_id(repo_root)


def cache_version(extraction_signature: str) -> str:
    return "|".join(
        (
            str(CACHE_FORMAT),
            porttool.__version__,
            f"py{sys.version_info[0]}.{sys.version_info[1]}",
            extraction_signature,
        )
    )


# --------------------------------------------------------------------------- blobs


class _NoGlobals(pickle.Unpickler):
    """Unpickler for blobs made of builtins only: any class or function is refused."""

    def find_class(self, module: str, name: str) -> object:
        raise pickle.UnpicklingError(f"cache entry refers to {module}.{name}")


def encode(records: FileRecords) -> bytes:
    return pickle.dumps(tuple(records), protocol=pickle.HIGHEST_PROTOCOL)


def decode(blob: bytes) -> FileRecords:
    """Records from a blob. Raises ``ValueError`` for anything that is not a valid entry."""
    try:
        value = _NoGlobals(io.BytesIO(blob)).load()
    except Exception as error:  # noqa: BLE001 - pickle raises many types for bad input
        raise ValueError(f"unreadable cache entry: {error}") from error
    if type(value) is not tuple or len(value) != FIELD_COUNT:
        raise ValueError("cache entry has an unexpected shape")
    records = FileRecords._make(value)
    if (
        records.format != CACHE_FORMAT
        or type(records.rel_path) is not str
        or type(records.size) is not int
        or type(records.mtime_ns) is not int
        or type(records.status) is not str
        or any(type(field) is not tuple for field in value[_FIRST_LIST_FIELD:])
    ):
        raise ValueError("cache entry has unexpected content")
    return records


# --------------------------------------------------------------------------- database


class RecordCache:
    """All methods are blocking and open their own connection: call them from any thread."""

    def __init__(self, directory: Path, version: str) -> None:
        self.directory = directory
        self.path = directory / DB_NAME
        self.version = version

    def _connect(self) -> sqlite3.Connection:
        self.directory.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=_BUSY_TIMEOUT_S, isolation_level=None)
        try:
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS entries ("
                "rel_path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, "
                "crc INTEGER NOT NULL, data BLOB NOT NULL)"
            )
            row = connection.execute("SELECT value FROM meta WHERE key = 'version'").fetchone()
            if row is None or row[0] != self.version:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("DELETE FROM entries")
                connection.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES ('version', ?)",
                    (self.version,),
                )
                connection.execute("COMMIT")
        except BaseException:
            connection.close()
            raise
        return connection

    def _delete_database(self) -> None:
        for suffix in ("", "-journal", "-wal", "-shm"):
            target = Path(f"{self.path}{suffix}")
            if target.exists():
                target.unlink()

    def _read_rows(self) -> list[tuple[str, int, int, int, bytes]]:
        with closing(self._connect()) as connection:
            rows: list[tuple[str, int, int, int, bytes]] = connection.execute(
                "SELECT rel_path, size, mtime_ns, crc, data FROM entries"
            ).fetchall()
        return rows

    def read(
        self,
        wanted: Mapping[str, tuple[int, int]],
        check_cancelled: Callable[[], None] = lambda: None,
    ) -> CacheRead:
        """Return the entries that are valid for ``wanted`` (rel path -> (size, mtime_ns))."""
        findings: list[Finding] = []
        try:
            try:
                rows = self._read_rows()
            except sqlite3.DatabaseError as error:
                if getattr(error, "sqlite_errorcode", None) not in (
                    _SQLITE_CORRUPT,
                    _SQLITE_NOTADB,
                ):
                    raise
                # The cache file itself is damaged: delete it and start again, empty.
                self._delete_database()
                rows = self._read_rows()
                findings.append(
                    Finding(
                        code=codes.CACHE_RESET,
                        severity=Severity.INFO,
                        message="The cache file was unreadable. It was deleted and is rebuilt.",
                        file=str(self.path),
                    )
                )
        except (sqlite3.Error, OSError) as error:
            raise CacheUnavailable(str(error)) from error
        hits: dict[str, FileRecords] = {}
        obsolete: list[str] = []
        for rel_path, size, mtime_ns, crc, data in rows:
            fingerprint = wanted.get(rel_path)
            if fingerprint is None:
                obsolete.append(rel_path)
                continue
            if fingerprint != (size, mtime_ns):
                continue  # the file changed: the entry is replaced after the parse
            check_cancelled()
            try:
                if zlib.crc32(data) != crc:
                    raise ValueError("checksum mismatch")
                records = decode(data)
                if records.rel_path != rel_path or (records.size, records.mtime_ns) != fingerprint:
                    raise ValueError("entry does not describe this file")
            except ValueError as error:
                obsolete.append(rel_path)
                findings.append(
                    Finding(
                        code=codes.CACHE_ENTRY_CORRUPT,
                        severity=Severity.INFO,
                        message=f"A cache entry was unreadable ({error}). It was deleted and "
                        "the file was parsed again.",
                        file=rel_path,
                    )
                )
                continue
            hits[rel_path] = records
        return CacheRead(hits=hits, obsolete=tuple(obsolete), findings=tuple(findings))

    def write(self, records: Iterable[FileRecords], delete: Iterable[str] = ()) -> None:
        """Store entries and delete rows, in one transaction."""
        rows = []
        for item in records:
            blob = encode(item)
            rows.append((item.rel_path, item.size, item.mtime_ns, zlib.crc32(blob), blob))
        doomed = [(rel_path,) for rel_path in delete]
        if not rows and not doomed:
            return
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    connection.executemany("DELETE FROM entries WHERE rel_path = ?", doomed)
                    connection.executemany(
                        "INSERT OR REPLACE INTO entries (rel_path, size, mtime_ns, crc, data) "
                        "VALUES (?, ?, ?, ?, ?)",
                        rows,
                    )
                    connection.execute("COMMIT")
                except BaseException:
                    connection.execute("ROLLBACK")
                    raise
        except (sqlite3.Error, OSError) as error:
            raise CacheUnavailable(str(error)) from error
