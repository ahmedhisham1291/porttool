"""File utilities: atomic writes, long Windows paths, fingerprints and the per-repo lock.

Every file PortTool writes goes through :func:`atomic_write_bytes` or
:func:`atomic_write_text`: the content lands in a temporary file in the target folder,
is flushed and synced, and replaces the target in one ``os.replace`` call. A reader
never sees a half-written file, and a crash leaves either the old or the new content.

Failures raise :class:`FileWriteError` (a ``WriteError``). A locked or read-only file is
an expected problem for the callers: ``error.to_finding()`` turns it into a ``Finding``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import ntpath
import os
import re
import secrets
import stat
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

import psutil

from porttool.core.errors import RepoStateError, WriteError
from porttool.core.models import SourceFile
from porttool.core.results import Finding, Severity

_LOG = logging.getLogger(__name__)

# --------------------------------------------------------------------------- app folders

APP_DIR_NAME = "PortTool"


def local_app_dir() -> Path:
    r"""Return ``%LOCALAPPDATA%\PortTool`` (logs, cache and lock files live below it)."""
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    return root / APP_DIR_NAME


def repo_id(repo_root: str | os.PathLike[str]) -> str:
    """Return a stable, file-name-safe identifier of a repo folder, e.g. ``MyEcu-3f9a1c0b7d2e41aa``.

    Two spellings of the same folder (case, slashes, trailing separator) give the same id.
    """
    normal = os.path.normcase(os.path.abspath(repo_root))
    digest = hashlib.sha1(normal.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", os.path.basename(normal))[:40] or "repo"
    return f"{name}-{digest}"


def default_cache_dir(repo_root: str | os.PathLike[str]) -> Path:
    r"""Return ``%LOCALAPPDATA%\PortTool\cache\<repo id>``: the default ``cache_dir``."""
    return local_app_dir() / "cache" / repo_id(repo_root)


def default_lock_dir() -> Path:
    r"""Return ``%LOCALAPPDATA%\PortTool\locks``. Lock files stay out of the ECU repo on purpose:
    a lock file inside the repo would make the git working tree dirty."""
    return local_app_dir() / "locks"


# --------------------------------------------------------------------------- long paths

#: Absolute Windows paths longer than this get the extended-length prefix. 248 is the
#: limit for folders (MAX_PATH minus an 8.3 file name), so one rule covers files and folders.
LONG_PATH_THRESHOLD = 248

_EXTENDED_PREFIX = "\\\\?\\"
_EXTENDED_UNC_PREFIX = "\\\\?\\UNC\\"
_DEVICE_PREFIX = "\\\\.\\"


def long_path(path: str | os.PathLike[str]) -> str:
    r"""Add the ``\\?\`` prefix to an absolute Windows path longer than 248 characters.

    Pure string logic. ``C:\dir\file`` becomes ``\\?\C:\dir\file`` and ``\\server\share\file``
    becomes ``\\?\UNC\server\share\file``. The prefix switches off Windows' own path
    normalisation, so forward slashes, ``.`` and ``..`` are resolved here. Short paths,
    relative paths, drive-relative paths (``C:file``) and paths that already carry a
    ``\\?\`` or ``\\.\`` prefix come back unchanged: make a path absolute before calling this.
    """
    text = os.fspath(path)
    if len(text) <= LONG_PATH_THRESHOLD:
        return text
    if text.startswith((_EXTENDED_PREFIX, _DEVICE_PREFIX)):
        return text
    drive, tail = ntpath.splitdrive(text)
    if not drive or not tail.startswith(("\\", "/")):
        return text
    normal = ntpath.normpath(text)
    if normal.startswith("\\\\"):
        return _EXTENDED_UNC_PREFIX + normal[2:]
    return _EXTENDED_PREFIX + normal


# --------------------------------------------------------------------------- errors


class FileWriteError(WriteError):
    """A file could not be written. Knows the path and converts to a ``Finding``."""

    finding_code = "FS-WRITE-001"

    def __init__(self, message: str, *, path: Path, detail: str | None = None) -> None:
        super().__init__(message, detail=detail)
        self.path = path

    def to_finding(self, repo_root: str | os.PathLike[str] | None = None) -> Finding:
        """Return this problem as an error ``Finding`` (``file`` is repo-relative if possible)."""
        return Finding(
            code=self.finding_code,
            severity=Severity.ERROR,
            message=self.user_message,
            file=repo_relative(self.path, repo_root),
        )


class ReadOnlyFileError(FileWriteError):
    """The target file is read-only. PortTool never removes that protection by itself."""

    finding_code = "FS-READONLY-001"


class FileLockedError(FileWriteError):
    """Another program kept the target file open through every retry."""

    finding_code = "FS-LOCKED-001"


# --------------------------------------------------------------------------- atomic writes

#: Retries after the first refused ``os.replace`` (so four attempts in total).
REPLACE_RETRIES = 3
#: Wait before the first retry; doubled for each further retry (0.1 s, 0.2 s, 0.4 s).
REPLACE_BACKOFF_S = 0.1

#: Temporary files are named ``.porttool-<pid>-<random>.tmp``. A crash can leave one behind;
#: the pipeline's recovery may delete files that match this prefix and suffix.
TEMP_PREFIX = ".porttool-"
TEMP_SUFFIX = ".tmp"


def _temp_path(folder: Path) -> Path:
    # Short, fixed-length name: the temporary path is never much longer than the folder path.
    return folder / f"{TEMP_PREFIX}{os.getpid()}-{secrets.token_hex(6)}{TEMP_SUFFIX}"


def _discard(temp: Path) -> None:
    try:
        os.unlink(long_path(temp))
    except FileNotFoundError:
        return
    except OSError as error:
        _LOG.warning("could not remove temporary file %s: %s", temp, error)


def _refuse_read_only(target: Path) -> None:
    try:
        mode = os.stat(long_path(target)).st_mode
    except FileNotFoundError:
        return
    except OSError as error:
        raise FileWriteError(
            f"{target} cannot be checked before writing: {error.strerror or error}",
            path=target,
            detail=repr(error),
        ) from error
    if stat.S_ISDIR(mode):
        raise FileWriteError(f"{target} is a folder, not a file.", path=target)
    # On Windows S_IWRITE is cleared exactly when the read-only attribute is set.
    if not mode & stat.S_IWRITE:
        raise ReadOnlyFileError(
            f"{target} is read-only. Remove the write protection (or check the file out) "
            "and try again.",
            path=target,
        )


def _replace_with_retry(temp: Path, target: Path, retries: int, backoff_s: float) -> None:
    attempt = 0
    while True:
        try:
            os.replace(long_path(temp), long_path(target))
            return
        except PermissionError as error:
            # Windows reports a sharing violation (another program has the file open) and
            # "access denied" as PermissionError. Both often clear within a moment.
            if attempt >= retries:
                raise FileLockedError(
                    f"{target} is in use by another program (tried {attempt + 1} times). "
                    "Close the program that has the file open and try again.",
                    path=target,
                    detail=repr(error),
                ) from error
            delay = backoff_s * (2**attempt)
            attempt += 1
            _LOG.warning(
                "replacing %s was refused (%s); retry %d of %d in %.2f s",
                target,
                error,
                attempt,
                retries,
                delay,
            )
            time.sleep(delay)
        except OSError as error:
            raise FileWriteError(
                f"{target} could not be replaced: {error.strerror or error}",
                path=target,
                detail=repr(error),
            ) from error


def atomic_write_bytes(
    path: str | os.PathLike[str],
    data: bytes,
    *,
    retries: int = REPLACE_RETRIES,
    backoff_s: float = REPLACE_BACKOFF_S,
) -> None:
    """Write ``data`` to ``path`` so that the file is always either the old or the new content.

    The target folder must exist. Raises :class:`ReadOnlyFileError` for a read-only target,
    :class:`FileLockedError` when another program keeps the file open through ``retries``
    retries, and :class:`FileWriteError` for every other failure. The target is untouched
    and no temporary file is left behind when it fails. Blocking: call it from a worker thread.
    """
    target = Path(os.path.abspath(path))
    folder = target.parent
    if not os.path.isdir(long_path(folder)):
        raise FileWriteError(f"The folder {folder} does not exist.", path=target)
    _refuse_read_only(target)
    temp = _temp_path(folder)
    try:
        with open(long_path(temp), "xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        _discard(temp)
        raise FileWriteError(
            f"A temporary file next to {target} could not be written: {error.strerror or error}",
            path=target,
            detail=repr(error),
        ) from error
    try:
        _replace_with_retry(temp, target, retries, backoff_s)
    except BaseException:
        _discard(temp)
        raise


def atomic_write_text(
    path: str | os.PathLike[str],
    text: str,
    *,
    encoding: str = "utf-8",
    newline: str | None = None,
    retries: int = REPLACE_RETRIES,
    backoff_s: float = REPLACE_BACKOFF_S,
) -> None:
    r"""Encode ``text`` and write it atomically, see :func:`atomic_write_bytes`.

    ``newline=None`` writes the line ends exactly as they are in ``text``. ``"\n"`` or
    ``"\r\n"`` first normalises every line end, then writes that one.
    """
    if newline is not None:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if newline != "\n":
            text = text.replace("\n", newline)
    atomic_write_bytes(path, text.encode(encoding), retries=retries, backoff_s=backoff_s)


# --------------------------------------------------------------------------- fingerprints

_HASH_CHUNK = 1024 * 1024


def repo_relative(
    path: str | os.PathLike[str], repo_root: str | os.PathLike[str] | None = None
) -> str:
    """Return ``path`` relative to ``repo_root`` with forward slashes.

    A path outside the repo (or ``repo_root=None``) comes back absolute, with forward slashes.
    """
    absolute = Path(os.path.abspath(path))
    if repo_root is not None:
        root = Path(os.path.abspath(repo_root))
        if absolute.is_relative_to(root):
            return absolute.relative_to(root).as_posix()
    return absolute.as_posix()


def sha256_file(path: str | os.PathLike[str]) -> str:
    """Return the SHA-256 hex digest of a file, read in chunks. Raises ``OSError``."""
    digest = hashlib.sha256()
    with open(long_path(os.path.abspath(path)), "rb") as stream:
        while chunk := stream.read(_HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(
    path: str | os.PathLike[str],
    repo_root: str | os.PathLike[str] | None = None,
    *,
    with_sha256: bool = False,
) -> SourceFile:
    """Return the ``SourceFile`` fingerprint of ``path``: size, mtime_ns and, on request, sha256.

    Without the hash this is one ``stat`` call. Raises ``OSError`` (for example
    ``FileNotFoundError``) when the file cannot be read: the caller decides whether that
    is a ``Finding``.
    """
    absolute = os.path.abspath(path)
    extended = long_path(absolute)
    if not with_sha256:
        info = os.stat(extended)
        digest = None
    else:
        hasher = hashlib.sha256()
        with open(extended, "rb") as stream:
            info = os.fstat(stream.fileno())
            while chunk := stream.read(_HASH_CHUNK):
                hasher.update(chunk)
        digest = hasher.hexdigest()
    return SourceFile(
        rel_path=repo_relative(absolute, repo_root),
        size=info.st_size,
        mtime_ns=info.st_mtime_ns,
        sha256=digest,
    )


def is_unchanged(source: SourceFile, repo_root: str | os.PathLike[str]) -> bool:
    """Return True when the file on disk still matches the fingerprint ``source``.

    With a recorded sha256 the content decides (size is only the fast "no"). Without it,
    size and mtime_ns must both match. A missing or unreadable file counts as changed.
    """
    path = Path(source.rel_path)
    if not path.is_absolute():
        path = Path(repo_root) / path
    try:
        current = fingerprint(path, repo_root)
        if current.size != source.size:
            return False
        if source.sha256 is None:
            return current.mtime_ns == source.mtime_ns
        return sha256_file(path) == source.sha256
    except OSError as error:
        _LOG.info("fingerprint check: %s cannot be read (%s)", path, error)
        return False


# --------------------------------------------------------------------------- repo lock

#: Two start times of the same process, read at different moments, differ by less than this.
_START_TIME_TOLERANCE_S = 1.0
#: A lock file younger than this without readable content is "being written right now".
_FRESH_LOCK_GRACE_S = 2.0
_ACQUIRE_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class LockInfo:
    """Content of a lock file: who holds the repo."""

    pid: int
    start_time: float  # start time of the holding process, seconds since the epoch
    repo_root: str
    created_utc: str
    token: str  # unique per RepoLock object


class RepoLockedError(RepoStateError):
    """Another running PortTool instance works on this repo."""

    def __init__(self, message: str, *, lock_file: Path, holder: LockInfo | None) -> None:
        super().__init__(message, detail=str(lock_file))
        self.lock_file = lock_file
        self.holder = holder


def _process_alive(info: LockInfo) -> bool:
    """True when the process that wrote ``info`` still runs (same pid AND same start time)."""
    try:
        process = psutil.Process(info.pid)
        if process.status() == psutil.STATUS_ZOMBIE:
            return False
        started = float(process.create_time())
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        # The pid exists but belongs to another user: it cannot be proven dead.
        return True
    return abs(started - info.start_time) < _START_TIME_TOLERANCE_S


def read_lock(lock_file: str | os.PathLike[str]) -> LockInfo | None:
    """Return the content of a lock file, or None when it is missing or unreadable."""
    try:
        raw = Path(lock_file).read_bytes()
    except OSError:
        return None
    return _parse_lock(raw)


def _parse_lock(raw: bytes) -> LockInfo | None:
    try:
        data = json.loads(raw.decode("utf-8"))
        return LockInfo(
            pid=int(data["pid"]),
            start_time=float(data["start_time"]),
            repo_root=str(data["repo_root"]),
            created_utc=str(data["created_utc"]),
            token=str(data["token"]),
        )
    except (ValueError, KeyError, TypeError):
        return None


class RepoLock:
    """One PortTool instance per repo: a lock file with the holder's pid and start time.

    ::

        with RepoLock(config.repo_root):
            ...  # only this process works on the repo

    ``acquire`` raises :class:`RepoLockedError` when a live process holds the lock. A lock
    left by a process that no longer runs (crash, kill, power loss) is stale: it is
    detected by pid plus process start time, logged and replaced. The lock is not
    re-entrant: a second ``RepoLock`` on the same repo in the same process is refused too.
    """

    def __init__(
        self,
        repo_root: str | os.PathLike[str],
        *,
        lock_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        self.repo_root = Path(os.path.abspath(repo_root))
        folder = Path(lock_dir) if lock_dir is not None else default_lock_dir()
        self.lock_file = folder / f"{repo_id(self.repo_root)}.lock"
        self._token = secrets.token_hex(8)
        self._held = False

    @property
    def held(self) -> bool:
        return self._held

    def holder(self) -> LockInfo | None:
        """Who holds the lock according to the lock file (None: nobody, or unreadable)."""
        return read_lock(self.lock_file)

    def acquire(self) -> None:
        if self._held:
            raise RepoLockedError(
                f"This RepoLock already holds {self.repo_root}.",
                lock_file=self.lock_file,
                holder=self.holder(),
            )
        try:
            self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise RepoStateError(
                f"The lock folder {self.lock_file.parent} cannot be created: "
                f"{error.strerror or error}",
                detail=repr(error),
            ) from error
        for _ in range(_ACQUIRE_ATTEMPTS):
            if self._try_create():
                self._held = True
                return
            self._check_existing()
        raise RepoLockedError(
            f"The lock for {self.repo_root} could not be taken: another PortTool instance "
            "keeps taking it. Try again.",
            lock_file=self.lock_file,
            holder=self.holder(),
        )

    def release(self) -> None:
        """Give the lock back. Safe to call when it is not held."""
        if not self._held:
            return
        self._held = False
        current = read_lock(self.lock_file)
        if current is None or current.token != self._token:
            _LOG.warning(
                "lock file %s no longer belongs to this process; it is left alone", self.lock_file
            )
            return
        try:
            os.unlink(self.lock_file)
        except FileNotFoundError:
            return
        except OSError as error:
            _LOG.warning("lock file %s could not be removed: %s", self.lock_file, error)

    def __enter__(self) -> RepoLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()

    # ---- internals

    def _try_create(self) -> bool:
        info = LockInfo(
            pid=os.getpid(),
            start_time=float(psutil.Process().create_time()),
            repo_root=self.repo_root.as_posix(),
            created_utc=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            token=self._token,
        )
        payload = json.dumps(asdict(info), indent=2).encode("utf-8")
        try:
            descriptor = os.open(self.lock_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            return False
        except OSError as error:
            raise RepoStateError(
                f"The lock file {self.lock_file} cannot be created: {error.strerror or error}",
                detail=repr(error),
            ) from error
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        return True

    def _check_existing(self) -> None:
        """Raise if a live process holds the lock; remove the lock file if it is stale."""
        try:
            raw = self.lock_file.read_bytes()
            age_s = time.time() - self.lock_file.stat().st_mtime
        except FileNotFoundError:
            return  # released in the meantime: the caller tries again
        except OSError as error:
            raise RepoLockedError(
                f"The lock file {self.lock_file} exists but cannot be read "
                f"({error.strerror or error}). Another PortTool instance may be using "
                f"{self.repo_root}.",
                lock_file=self.lock_file,
                holder=None,
            ) from error
        holder = _parse_lock(raw)
        if holder is None:
            if age_s < _FRESH_LOCK_GRACE_S:
                raise RepoLockedError(
                    f"Another PortTool instance is starting on {self.repo_root}. Try again "
                    "in a moment.",
                    lock_file=self.lock_file,
                    holder=None,
                )
            _LOG.warning("removing unreadable lock file %s", self.lock_file)
        elif _process_alive(holder):
            who = "this process" if holder.pid == os.getpid() else f"process {holder.pid}"
            raise RepoLockedError(
                f"{self.repo_root} is in use by another PortTool instance ({who}, since "
                f"{holder.created_utc}). Close it first.",
                lock_file=self.lock_file,
                holder=holder,
            )
        else:
            _LOG.warning(
                "removing stale lock file %s: process %d no longer runs",
                self.lock_file,
                holder.pid,
            )
        self._remove_if_unchanged(raw)

    def _remove_if_unchanged(self, judged: bytes) -> None:
        # Only remove the content that was judged stale. If another instance replaced the
        # file in the meantime, its fresh lock must survive. (A tiny window remains between
        # this comparison and the unlink; see HANDOFF.md.)
        try:
            if self.lock_file.read_bytes() != judged:
                return
            os.unlink(self.lock_file)
        except FileNotFoundError:
            return
        except OSError as error:
            raise RepoLockedError(
                f"The stale lock file {self.lock_file} cannot be removed "
                f"({error.strerror or error}). Delete it by hand.",
                lock_file=self.lock_file,
                holder=None,
            ) from error
