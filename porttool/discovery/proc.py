"""Running an external program for discovery: argument list, timeout, whole-tree kill.

Output goes to temporary files instead of pipes. A grandchild that keeps a pipe open
can then never block us, and a tool that prints megabytes cannot fill a pipe buffer.
"""

from __future__ import annotations

import subprocess
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import psutil

DEFAULT_MAX_BYTES = 256 * 1024 * 1024
KILL_WAIT_S = 5.0


@dataclass(frozen=True, slots=True)
class ProcResult:
    argv: tuple[str, ...]
    exit_code: int | None  # None: the program never started or was killed
    timed_out: bool
    duration_s: float
    stdout: bytes
    stderr: bytes
    truncated: bool = False  # output was longer than the read limit
    spawn_error: str | None = None  # exception type name when the program did not start
    killed: int = 0  # processes killed after a timeout (the program and its children)
    survivors: int = 0  # processes that were still alive after the kill

    @property
    def started(self) -> bool:
        return self.spawn_error is None

    @property
    def ok(self) -> bool:
        return self.started and not self.timed_out and self.exit_code == 0


def hidden_window_options() -> dict[str, Any]:
    """Popen options that keep a console program from opening a window on Windows.

    The names exist only in the Windows build of the standard library; where they are
    absent this returns no options. Behaviour on a real desktop is unverified here.
    """
    options: dict[str, Any] = {}
    flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if flags:
        options["creationflags"] = flags
    startupinfo_type = getattr(subprocess, "STARTUPINFO", None)
    if startupinfo_type is not None:
        info = startupinfo_type()
        info.dwFlags |= int(getattr(subprocess, "STARTF_USESHOWWINDOW", 1))
        info.wShowWindow = int(getattr(subprocess, "SW_HIDE", 0))
        options["startupinfo"] = info
    return options


def kill_process_tree(pid: int, wait_s: float = KILL_WAIT_S) -> tuple[int, int]:
    """Kill ``pid`` and every descendant. Returns (processes killed, still alive)."""
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return 0, 0
    try:
        family = parent.children(recursive=True)
    except psutil.Error:
        family = []
    family.append(parent)
    killed = 0
    for process in family:
        try:
            process.kill()
        except psutil.Error:
            continue  # already gone, or not ours to kill: wait_procs below tells which
        killed += 1
    _, alive = psutil.wait_procs(family, timeout=wait_s)
    return killed, len(alive)


def _read_limited(handle: Any, limit: int) -> tuple[bytes, bool]:
    handle.seek(0)
    data: bytes = handle.read(limit + 1)
    return data[:limit], len(data) > limit


def run_captured(
    argv: Sequence[str],
    *,
    timeout_s: float,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> ProcResult:
    """Run ``argv`` without a shell, stdin closed, and capture what it prints.

    Never raises for a program that is missing, fails or hangs: the result says what
    happened. On timeout the whole process tree is killed.
    """
    command = tuple(argv)
    started = time.monotonic()
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            process = subprocess.Popen(  # noqa: S603 - argument list, never a shell
                list(command),
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                cwd=cwd,
                env=dict(env) if env is not None else None,
                shell=False,
                **hidden_window_options(),
            )
        except (OSError, ValueError) as error:
            return ProcResult(
                argv=command,
                exit_code=None,
                timed_out=False,
                duration_s=time.monotonic() - started,
                stdout=b"",
                stderr=b"",
                spawn_error=type(error).__name__,
            )
        timed_out = False
        killed = survivors = 0
        exit_code: int | None
        try:
            exit_code = process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            killed, survivors = kill_process_tree(process.pid)
            try:
                process.wait(timeout=KILL_WAIT_S)
            except subprocess.TimeoutExpired:
                survivors = max(survivors, 1)
            exit_code = None
        duration = time.monotonic() - started
        stdout, cut_out = _read_limited(out, max_bytes)
        stderr, cut_err = _read_limited(err, max_bytes)
    return ProcResult(
        argv=command,
        exit_code=exit_code,
        timed_out=timed_out,
        duration_s=duration,
        stdout=stdout,
        stderr=stderr,
        truncated=cut_out or cut_err,
        killed=killed,
        survivors=survivors,
    )


def _try_decode(data: bytes, codec: str) -> str | None:
    try:
        return data.decode(codec)
    except UnicodeDecodeError:
        return None


def decode_output(data: bytes) -> tuple[str, str]:
    """Decode program output without losing a byte: (text, name of the decoding used)."""
    if not data:
        return "", "empty"
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = _try_decode(data, "utf-16")
        if text is not None:
            return text, "utf-16"
    text = _try_decode(data, "utf-8-sig")
    if text is not None:
        return text, "utf-8"
    return data.decode("utf-8", errors="backslashreplace"), "utf-8 with \\x escapes"
