"""Tests for porttool.core.fsutil: atomic writes, long paths, fingerprints, the repo lock."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from porttool.core import fsutil
from porttool.core.errors import RepoStateError, WriteError
from porttool.core.fsutil import (
    FileLockedError,
    FileWriteError,
    LockInfo,
    ReadOnlyFileError,
    RepoLock,
    RepoLockedError,
    atomic_write_bytes,
    atomic_write_text,
    fingerprint,
    is_unchanged,
    long_path,
    read_lock,
    repo_id,
    repo_relative,
    sha256_file,
)
from porttool.core.results import Severity

REPO_ROOT = Path(__file__).resolve().parents[3]


def _temp_files(folder: Path) -> list[str]:
    return [name for name in os.listdir(folder) if name.endswith(fsutil.TEMP_SUFFIX)]


# --------------------------------------------------------------------------- atomic writes


def test_atomic_write_creates_and_replaces(tmp_path: Path) -> None:
    target = tmp_path / "a.arxml"
    atomic_write_bytes(target, b"one")
    assert target.read_bytes() == b"one"
    atomic_write_bytes(target, b"two")
    assert target.read_bytes() == b"two"
    assert _temp_files(tmp_path) == []


def test_path_with_spaces_and_non_ascii_characters(tmp_path: Path) -> None:
    folder = tmp_path / "Mein Projekt ÄÖÜ" / "مجلد" / "配置 フォルダ"
    folder.mkdir(parents=True)
    target = folder / "Größe und Maß.arxml"
    atomic_write_text(target, "<a>ß</a>\n")
    assert target.read_text(encoding="utf-8") == "<a>ß</a>\n"
    source = fingerprint(target, tmp_path, with_sha256=True)
    assert source.rel_path == "Mein Projekt ÄÖÜ/مجلد/配置 フォルダ/Größe und Maß.arxml"
    assert is_unchanged(source, tmp_path)
    assert _temp_files(folder) == []


def test_atomic_write_text_newline_handling(tmp_path: Path) -> None:
    target = tmp_path / "t.txt"
    atomic_write_text(target, "a\r\nb\nc\r")
    assert target.read_bytes() == b"a\r\nb\nc\r"
    atomic_write_text(target, "a\r\nb\nc\r", newline="\r\n")
    assert target.read_bytes() == b"a\r\nb\r\nc\r\n"
    atomic_write_text(target, "a\r\nb\nc", newline="\n")
    assert target.read_bytes() == b"a\nb\nc"
    atomic_write_text(target, "ä", encoding="latin-1")
    assert target.read_bytes() == b"\xe4"


def test_read_only_target_file(tmp_path: Path) -> None:
    target = tmp_path / "locked.arxml"
    target.write_bytes(b"original")
    target.chmod(stat.S_IREAD)
    try:
        with pytest.raises(ReadOnlyFileError) as caught:
            atomic_write_bytes(target, b"new")
    finally:
        target.chmod(stat.S_IREAD | stat.S_IWRITE)
    assert isinstance(caught.value, WriteError)
    assert "read-only" in caught.value.user_message
    assert target.read_bytes() == b"original"
    assert _temp_files(tmp_path) == []
    finding = caught.value.to_finding(tmp_path)
    assert finding.code == "FS-READONLY-001"
    assert finding.severity is Severity.ERROR
    assert finding.file == "locked.arxml"


@pytest.mark.parametrize("failures", [1, 2, 3])
def test_file_locked_by_another_process_is_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failures: int
) -> None:
    target = tmp_path / "busy.arxml"
    target.write_bytes(b"old")
    real_replace = os.replace
    calls: list[int] = []
    sleeps: list[float] = []

    def flaky_replace(src: str, dst: str) -> None:
        calls.append(1)
        if len(calls) <= failures:
            raise PermissionError(13, "The process cannot access the file", str(dst))
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky_replace)
    monkeypatch.setattr(time, "sleep", sleeps.append)
    atomic_write_bytes(target, b"new")
    assert target.read_bytes() == b"new"
    assert len(calls) == failures + 1
    assert sleeps == [0.1, 0.2, 0.4][:failures]
    assert _temp_files(tmp_path) == []


def test_file_locked_by_another_process_gives_up_after_three_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "busy.arxml"
    target.write_bytes(b"old")
    calls: list[int] = []

    def always_locked(src: str, dst: str) -> None:
        calls.append(1)
        raise PermissionError(13, "The process cannot access the file", str(dst))

    monkeypatch.setattr(os, "replace", always_locked)
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    with pytest.raises(FileLockedError) as caught:
        atomic_write_bytes(target, b"new")
    assert len(calls) == 4  # the first attempt plus three retries
    assert "in use by another program" in caught.value.user_message
    assert caught.value.to_finding(tmp_path).code == "FS-LOCKED-001"
    assert target.read_bytes() == b"old"
    assert _temp_files(tmp_path) == []


def test_other_replace_failure_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "x.arxml"
    calls: list[int] = []

    def broken(src: str, dst: str) -> None:
        calls.append(1)
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", broken)
    with pytest.raises(FileWriteError) as caught:
        atomic_write_bytes(target, b"new")
    assert not isinstance(caught.value, FileLockedError)
    assert len(calls) == 1
    assert "No space left" in caught.value.user_message
    assert not target.exists()
    assert _temp_files(tmp_path) == []


def test_write_into_missing_folder_fails_clearly(tmp_path: Path) -> None:
    with pytest.raises(FileWriteError, match="does not exist"):
        atomic_write_bytes(tmp_path / "nope" / "a.txt", b"x")


def test_write_onto_a_folder_fails_clearly(tmp_path: Path) -> None:
    (tmp_path / "dir").mkdir()
    with pytest.raises(FileWriteError, match="is a folder"):
        atomic_write_bytes(tmp_path / "dir", b"x")


def test_temporary_file_cannot_be_created(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(fsutil, "open", refuse, raising=False)
    with pytest.raises(FileWriteError, match="temporary file"):
        atomic_write_bytes(tmp_path / "a.txt", b"x")


def test_interrupted_write_leaves_no_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "a.txt"
    target.write_bytes(b"old")

    def interrupt(src: str, dst: str) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(os, "replace", interrupt)
    with pytest.raises(KeyboardInterrupt):
        atomic_write_bytes(target, b"new")
    assert target.read_bytes() == b"old"
    assert _temp_files(tmp_path) == []


# --------------------------------------------------------------------------- long paths


def test_long_path_short_paths_are_unchanged() -> None:
    assert long_path("C:\\repo\\file.arxml") == "C:\\repo\\file.arxml"
    assert long_path("C:/repo/file.arxml") == "C:/repo/file.arxml"
    assert long_path(Path("relative/file.arxml")) == os.fspath(Path("relative/file.arxml"))


def test_long_path_threshold_is_248_characters() -> None:
    at_limit = "C:\\" + "a" * 245
    over_limit = "C:\\" + "a" * 246
    assert len(at_limit) == 248 and len(over_limit) == 249
    assert long_path(at_limit) == at_limit
    assert long_path(over_limit) == "\\\\?\\" + over_limit


def test_path_longer_than_260_characters_gets_the_extended_prefix() -> None:
    path = "D:\\ECU\\" + "\\".join(["Sehr Langer Ordner ÄÖ"] * 14) + "\\Datei.arxml"
    assert len(path) > 260
    assert long_path(path) == "\\\\?\\" + path


def test_long_path_normalises_slashes_and_dot_segments() -> None:
    path = "C:/repo/" + "x" * 250 + "/./sub/../file.arxml"
    assert long_path(path) == "\\\\?\\C:\\repo\\" + "x" * 250 + "\\file.arxml"


def test_long_path_unc_share() -> None:
    path = "\\\\server\\share\\" + "d" * 250 + "\\f.arxml"
    assert long_path(path) == "\\\\?\\UNC\\server\\share\\" + "d" * 250 + "\\f.arxml"
    forward = "//server/share/" + "d" * 250 + "/f.arxml"
    assert long_path(forward) == "\\\\?\\UNC\\server\\share\\" + "d" * 250 + "\\f.arxml"


def test_long_path_leaves_prefixed_relative_and_drive_relative_paths_alone() -> None:
    prefixed = "\\\\?\\C:\\" + "a" * 300
    device = "\\\\.\\pipe\\" + "a" * 300
    relative = "sub\\" + "a" * 300
    drive_relative = "C:" + "a" * 300
    for path in (prefixed, device, relative, drive_relative):
        assert long_path(path) == path


def test_long_posix_path_is_written_unprefixed(tmp_path: Path) -> None:
    # In this sandbox paths have no drive letter, so long_path must not touch them.
    folder = tmp_path
    for _ in range(6):
        folder = folder / ("d" * 50)
    folder.mkdir(parents=True)
    target = folder / "file.arxml"
    assert len(str(target)) > 260
    atomic_write_bytes(target, b"deep")
    assert target.read_bytes() == b"deep"
    assert fingerprint(target, tmp_path).size == 4


@pytest.mark.windows_only
@pytest.mark.skipif(os.name != "nt", reason="needs the Windows file API")
def test_write_below_a_path_longer_than_260_characters_on_windows(tmp_path: Path) -> None:
    folder = tmp_path
    for _ in range(6):
        folder = folder / ("d" * 50)
    os.makedirs(long_path(folder))
    target = folder / "file.arxml"
    atomic_write_bytes(target, b"deep")
    with open(long_path(target), "rb") as stream:
        assert stream.read() == b"deep"


# --------------------------------------------------------------------------- fingerprints


def test_fingerprint_fields(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "a.arxml"
    target.parent.mkdir()
    target.write_bytes(b"hello")
    plain = fingerprint(target, tmp_path)
    assert plain.rel_path == "sub/a.arxml"
    assert plain.size == 5
    assert plain.mtime_ns == target.stat().st_mtime_ns
    assert plain.sha256 is None
    hashed = fingerprint(target, tmp_path, with_sha256=True)
    assert hashed.sha256 == hashlib.sha256(b"hello").hexdigest() == sha256_file(target)
    assert (hashed.size, hashed.mtime_ns) == (plain.size, plain.mtime_ns)


def test_fingerprint_of_missing_file_raises_oserror(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        fingerprint(tmp_path / "gone.arxml", tmp_path)
    with pytest.raises(FileNotFoundError):
        fingerprint(tmp_path / "gone.arxml", tmp_path, with_sha256=True)


def test_repo_relative_outside_the_repo_is_absolute(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    outside = tmp_path / "other" / "x.arxml"
    assert repo_relative(outside, repo) == outside.as_posix()
    assert repo_relative(repo / "a" / "b.arxml", repo) == "a/b.arxml"
    assert repo_relative(outside) == outside.as_posix()


def test_is_unchanged_detects_edits(tmp_path: Path) -> None:
    target = tmp_path / "a.arxml"
    target.write_bytes(b"hello")
    by_stat = fingerprint(target, tmp_path)
    by_hash = fingerprint(target, tmp_path, with_sha256=True)
    assert is_unchanged(by_stat, tmp_path) and is_unchanged(by_hash, tmp_path)

    # Same size, same mtime, different content: only the hash notices.
    target.write_bytes(b"HELLO")
    os.utime(target, ns=(by_stat.mtime_ns, by_stat.mtime_ns))
    assert is_unchanged(by_stat, tmp_path)
    assert not is_unchanged(by_hash, tmp_path)

    # Same content, new mtime: the stat fingerprint reports a change, the hash does not.
    target.write_bytes(b"hello")
    os.utime(target, ns=(by_stat.mtime_ns + 5_000_000_000, by_stat.mtime_ns + 5_000_000_000))
    assert not is_unchanged(by_stat, tmp_path)
    assert is_unchanged(by_hash, tmp_path)

    target.write_bytes(b"longer content")
    assert not is_unchanged(by_stat, tmp_path) and not is_unchanged(by_hash, tmp_path)
    target.unlink()
    assert not is_unchanged(by_stat, tmp_path) and not is_unchanged(by_hash, tmp_path)


# --------------------------------------------------------------------------- app folders


def test_local_app_dir_uses_localappdata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert fsutil.local_app_dir() == tmp_path / "Local" / "PortTool"
    assert fsutil.default_lock_dir() == tmp_path / "Local" / "PortTool" / "locks"
    cache = fsutil.default_cache_dir(tmp_path / "repo")
    assert cache.parent == tmp_path / "Local" / "PortTool" / "cache"
    assert cache.name == repo_id(tmp_path / "repo")


def test_local_app_dir_without_localappdata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert fsutil.local_app_dir() == Path.home() / "AppData" / "Local" / "PortTool"


def test_repo_id_is_stable_and_file_name_safe(tmp_path: Path) -> None:
    repo = tmp_path / "My ECU (v2) ÄÖ"
    first = repo_id(repo)
    assert first == repo_id(str(repo) + os.sep) == repo_id(repo / "sub" / "..")
    assert first != repo_id(tmp_path / "other")
    assert all(char.isascii() and (char.isalnum() or char in "_-") for char in first)


# --------------------------------------------------------------------------- repo lock


def _write_lock(lock: RepoLock, pid: int, start_time: float) -> None:
    lock.lock_file.parent.mkdir(parents=True, exist_ok=True)
    info = {
        "pid": pid,
        "start_time": start_time,
        "repo_root": lock.repo_root.as_posix(),
        "created_utc": "2026-01-01T00:00:00Z",
        "token": "someone-else",
    }
    lock.lock_file.write_text(json.dumps(info), encoding="utf-8")


def _dead_pid() -> int:
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    assert not psutil.pid_exists(process.pid)
    return process.pid


def test_repo_lock_acquire_and_release(tmp_path: Path) -> None:
    lock = RepoLock(tmp_path / "repo", lock_dir=tmp_path / "locks")
    assert not lock.held and lock.holder() is None
    with lock:
        assert lock.held
        holder = lock.holder()
        assert holder is not None
        assert holder.pid == os.getpid()
        assert holder.repo_root == (tmp_path / "repo").as_posix()
        assert abs(holder.start_time - psutil.Process().create_time()) < 1.0
    assert not lock.held
    assert not lock.lock_file.exists()
    lock.release()  # releasing twice is harmless
    with lock:  # and the lock can be taken again
        assert lock.held


def test_repo_lock_default_folder_is_outside_the_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    repo = tmp_path / "repo"
    repo.mkdir()
    with RepoLock(repo) as lock:
        assert lock.lock_file.parent == tmp_path / "Local" / "PortTool" / "locks"
        assert os.listdir(repo) == []


def test_two_locks_on_different_repos_do_not_collide(tmp_path: Path) -> None:
    with RepoLock(tmp_path / "a", lock_dir=tmp_path / "locks"):
        with RepoLock(tmp_path / "b", lock_dir=tmp_path / "locks"):
            assert len(os.listdir(tmp_path / "locks")) == 2


def test_stale_lock_from_a_dead_pid_is_replaced(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    lock = RepoLock(tmp_path / "repo", lock_dir=tmp_path / "locks")
    _write_lock(lock, _dead_pid(), time.time() - 3600)
    with caplog.at_level("WARNING", logger="porttool"):
        lock.acquire()
    try:
        holder = lock.holder()
        assert holder is not None and holder.pid == os.getpid()
        assert "stale lock" in caplog.text
    finally:
        lock.release()


def test_stale_lock_from_a_reused_pid_is_replaced(tmp_path: Path) -> None:
    # The pid is alive (it is this very process) but it started at another time: the
    # process that wrote the lock is gone and its pid was handed out again.
    lock = RepoLock(tmp_path / "repo", lock_dir=tmp_path / "locks")
    _write_lock(lock, os.getpid(), psutil.Process().create_time() - 5000.0)
    with lock:
        holder = lock.holder()
        assert holder is not None and holder.token != "someone-else"


def test_live_lock_held_by_another_instance(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    locks = tmp_path / "locks"
    script = (
        "import sys\n"
        "from porttool.core.fsutil import RepoLock\n"
        "with RepoLock(sys.argv[1], lock_dir=sys.argv[2]):\n"
        "    print('held', flush=True)\n"
        "    sys.stdin.read()\n"
    )
    other = subprocess.Popen(
        [sys.executable, "-c", script, str(repo), str(locks)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        cwd=REPO_ROOT,
    )
    try:
        assert other.stdout is not None and other.stdin is not None
        assert other.stdout.readline().strip() == b"held"
        lock = RepoLock(repo, lock_dir=locks)
        with pytest.raises(RepoLockedError) as caught:
            lock.acquire()
        assert isinstance(caught.value, RepoStateError)
        assert caught.value.holder is not None and caught.value.holder.pid == other.pid
        assert f"process {other.pid}" in caught.value.user_message
        assert not lock.held
        lock.release()  # must not remove the other instance's lock file
        assert lock.lock_file.exists()

        other.stdin.close()  # the other instance finishes and releases
        assert other.wait(timeout=10) == 0
        with lock:
            assert lock.held
    finally:
        if other.poll() is None:
            other.kill()
            other.wait()


def test_second_lock_in_the_same_process_is_refused(tmp_path: Path) -> None:
    first = RepoLock(tmp_path / "repo", lock_dir=tmp_path / "locks")
    second = RepoLock(tmp_path / "repo", lock_dir=tmp_path / "locks")
    with first:
        with pytest.raises(RepoLockedError, match="this process"):
            second.acquire()
        with pytest.raises(RepoLockedError, match="already holds"):
            first.acquire()
    with second:
        assert second.held


def test_unreadable_fresh_lock_counts_as_live(tmp_path: Path) -> None:
    lock = RepoLock(tmp_path / "repo", lock_dir=tmp_path / "locks")
    lock.lock_file.parent.mkdir(parents=True)
    lock.lock_file.write_bytes(b"")  # another instance created it and has not written yet
    with pytest.raises(RepoLockedError, match="is starting"):
        lock.acquire()
    assert lock.lock_file.exists()


def test_unreadable_old_lock_is_replaced(tmp_path: Path) -> None:
    lock = RepoLock(tmp_path / "repo", lock_dir=tmp_path / "locks")
    lock.lock_file.parent.mkdir(parents=True)
    lock.lock_file.write_bytes(b"{not json")
    old = time.time() - 60
    os.utime(lock.lock_file, (old, old))
    with lock:
        assert read_lock(lock.lock_file) is not None


def test_release_leaves_a_lock_taken_over_by_someone_else(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    lock = RepoLock(tmp_path / "repo", lock_dir=tmp_path / "locks")
    lock.acquire()
    _write_lock(lock, os.getpid(), psutil.Process().create_time())
    with caplog.at_level("WARNING", logger="porttool"):
        lock.release()
    assert lock.lock_file.exists()
    assert "no longer belongs" in caplog.text


def test_process_owned_by_another_user_counts_as_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(pid: int) -> psutil.Process:
        raise psutil.AccessDenied(pid)

    monkeypatch.setattr(psutil, "Process", denied)
    info = LockInfo(pid=4, start_time=0.0, repo_root="x", created_utc="", token="t")
    assert fsutil._process_alive(info) is True


def test_lock_folder_cannot_be_created(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    lock = RepoLock(tmp_path / "repo", lock_dir=blocker / "locks")
    with pytest.raises(RepoStateError, match="cannot be created"):
        lock.acquire()


def test_lock_file_that_cannot_be_created_or_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = RepoLock(tmp_path / "repo", lock_dir=tmp_path / "locks")

    def denied(*args: object, **kwargs: object) -> int:
        raise PermissionError(13, "Access is denied")

    with monkeypatch.context() as patch:
        patch.setattr(os, "open", denied)
        with pytest.raises(RepoStateError, match="lock file .* cannot be created"):
            lock.acquire()
    assert not lock.held

    _write_lock(lock, os.getpid(), psutil.Process().create_time())
    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_bytes", denied)
        with pytest.raises(RepoLockedError, match="exists but cannot be read"):
            lock.acquire()
    assert lock.lock_file.exists()


def test_stale_lock_that_cannot_be_removed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock = RepoLock(tmp_path / "repo", lock_dir=tmp_path / "locks")
    _write_lock(lock, _dead_pid(), 1.0)

    def denied(path: object) -> None:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(os, "unlink", denied)
    with pytest.raises(RepoLockedError, match="stale lock file .* cannot be removed"):
        lock.acquire()


def test_lock_that_another_instance_keeps_taking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = RepoLock(tmp_path / "repo", lock_dir=tmp_path / "locks")
    monkeypatch.setattr(RepoLock, "_try_create", lambda self: False)
    with pytest.raises(RepoLockedError, match="could not be taken"):
        lock.acquire()  # the lock file vanished each time it was inspected, then reappeared


def test_release_when_the_lock_file_cannot_be_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    lock = RepoLock(tmp_path / "repo", lock_dir=tmp_path / "locks")
    lock.acquire()

    def denied(path: object) -> None:
        raise PermissionError(13, "Access is denied")

    with monkeypatch.context() as patch:
        patch.setattr(os, "unlink", denied)
        with caplog.at_level("WARNING", logger="porttool"):
            lock.release()
    assert "could not be removed" in caplog.text
    assert not lock.held
