"""Running one external tool. Every adapter of the davinci package goes through
``run_process``; nothing else in the package starts a process.

What it guarantees:

* argument list, never a shell; stdin closed; explicit working folder and environment;
  no console window (``CREATE_NO_WINDOW`` where the standard library has it);
* stdout and stderr are read concurrently; every line goes to the tool log file, and the
  newest line goes to ``ctx.progress`` at most ten times a second;
* memory is capped: only the last ``max_kept_lines`` lines and the first matching lines
  per kind are kept; the log file holds everything;
* on timeout, on cancel and on any error the whole process tree is killed with psutil
  (children first, then the tool), and the kill is confirmed;
* tools never overlap: one process-wide, first-come-first-served gate.

Unverified on Windows (see HANDOFF.md): the hidden window, the tree kill, and finding
children whose parent has already ended.
"""

from __future__ import annotations

import asyncio
import codecs
import locale
import subprocess
import threading
import time
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

import psutil

from porttool.core.errors import (
    JobCancelled,
    LicenseError,
    PortToolError,
    ToolRunError,
    ToolTimeoutError,
)
from porttool.core.interfaces import JobContext
from porttool.davinci.settings import ResultRules

#: How often the supervisor wakes up to look for cancel and timeout.
POLL_S = 0.1
#: How long the output pipes may stay open after the tool has ended.
PIPE_DRAIN_S = 2.0
#: How long a child of the tool may keep running after the tool has ended.
LEFTOVER_GRACE_S = 2.0
#: How long a killed process may take to disappear.
KILL_WAIT_S = 5.0
GATE_POLL_S = 0.05
PROGRESS_INTERVAL_S = 0.1
READ_CHUNK = 64 * 1024
MAX_LINE_BYTES = 64 * 1024
MAX_KEPT_LINE_CHARS = 2000
MAX_MATCHES = 20
_FIRST_SCAN_S = 0.05
_MAX_SCAN_INTERVAL_S = 2.0


# --------------------------------------------------------------------------- decoding


def ansi_code_page() -> str:
    """The ANSI code page of this machine (``cp1252`` on a western Windows)."""
    return locale.getencoding()


class LineDecoder:
    """Bytes in, text lines out, for one output stream.

    ``encoding="auto"`` decodes each line as UTF-8 and, if that fails, with the ANSI code
    page and replacement characters. Any other value is a codec name used for the whole
    stream, also with replacement characters. Nothing here can raise on bad bytes.
    """

    def __init__(self, encoding: str = "auto") -> None:
        self._auto = encoding == "auto"
        self._bytes = bytearray()
        self._text = ""
        self._first = True
        self._ansi = ansi_code_page()
        self._decoder = (
            None if self._auto else codecs.getincrementaldecoder(encoding)(errors="replace")
        )

    def _decode_auto(self, raw: bytes) -> str:
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode(self._ansi, errors="replace")

    def _emit(self, pieces: Iterable[str]) -> list[str]:
        lines: list[str] = []
        for piece in pieces:
            # A lone carriage return (a progress display) also ends a line.
            parts = [part for part in piece.split("\r") if part] or [""]
            for part in parts:
                if self._first:
                    self._first = False
                    part = part.removeprefix("﻿")
                lines.append(part)
        return lines

    def feed(self, chunk: bytes) -> list[str]:
        if self._decoder is not None:
            self._text += self._decoder.decode(chunk)
            *complete, self._text = self._text.split("\n")
            if len(self._text) > MAX_LINE_BYTES:
                complete.append(self._text)
                self._text = ""
            return self._emit(complete)
        self._bytes += chunk
        *raw_lines, rest = bytes(self._bytes).split(b"\n")
        if len(rest) > MAX_LINE_BYTES:
            raw_lines.append(rest)
            rest = b""
        self._bytes = bytearray(rest)
        return self._emit(self._decode_auto(raw) for raw in raw_lines)

    def flush(self) -> list[str]:
        """The last line when the stream ended without a line end."""
        if self._decoder is not None:
            rest = self._text + self._decoder.decode(b"", final=True)
            self._text = ""
        else:
            rest = self._decode_auto(bytes(self._bytes))
            self._bytes = bytearray()
        return self._emit([rest]) if rest else []


# --------------------------------------------------------------------------- judging lines


def judge_line(rules: ResultRules, text: str) -> str | None:
    """``"license"``, ``"error"``, ``"warning"`` or None for one line of tool output."""
    if any(pattern.search(text) for pattern in rules.license_patterns):
        return "license"
    reduced = text
    for pattern in rules.ignore_patterns:
        reduced = pattern.sub(" ", reduced)
    if any(pattern.search(reduced) for pattern in rules.error_patterns):
        return "error"
    if any(pattern.search(reduced) for pattern in rules.warning_patterns):
        return "warning"
    return None


@dataclass(frozen=True, slots=True)
class LogLine:
    line_no: int  # line number in the file the line was found in
    text: str
    stream: str = "out"


@dataclass(slots=True)
class Matches:
    """The first ``limit`` matching lines and the count of all of them."""

    limit: int = MAX_MATCHES
    lines: list[LogLine] = field(default_factory=list)
    total: int = 0

    def add(self, line: LogLine) -> None:
        self.total += 1
        if len(self.lines) < self.limit:
            self.lines.append(line)


def shorten(text: str, limit: int = MAX_KEPT_LINE_CHARS) -> str:
    return text if len(text) <= limit else text[:limit] + " ..."


# --------------------------------------------------------------------------- the log file


class ToolLog:
    """The complete output of one tool run, one numbered line per output line."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.line_no = 0
        path.parent.mkdir(parents=True, exist_ok=True)
        self._stream: TextIO | None = open(  # noqa: SIM115 - closed in close()
            path, "w", encoding="utf-8", errors="backslashreplace"
        )

    def write(self, text: str) -> int:
        """Write one line and return its line number (0 when the log is closed)."""
        if self._stream is None:
            return 0
        self.line_no += 1
        self._stream.write(text.replace("\n", " ") + "\n")
        return self.line_no

    def close(self) -> None:
        if self._stream is not None:
            stream, self._stream = self._stream, None
            stream.close()


# --------------------------------------------------------------------------- process family


@dataclass(frozen=True, slots=True)
class ProcessInfo:
    pid: int
    name: str

    def __str__(self) -> str:
        return f"{self.name} (pid {self.pid})"


def is_alive(process: Any) -> bool:
    """True while a psutil process runs. A zombie (ended, not yet collected) is not alive."""
    try:
        return bool(process.is_running()) and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False
    except psutil.Error:
        return True  # it exists but may not be inspected


def _info(process: Any) -> ProcessInfo:
    try:
        name = str(process.name())
    except psutil.Error:
        name = "?"
    return ProcessInfo(int(process.pid), name)


def orphans_of(
    pid: int,
    created_from: float,
    created_until: float,
    processes: Iterable[Any] | None = None,
) -> list[Any]:
    """Processes whose recorded parent is ``pid`` and that were created while it lived.

    This finds children of a tool that has already ended. Windows keeps the parent id of
    such a child. The creation-time window guards against the tool's process id having
    been given to a new, unrelated process.
    """
    found: list[Any] = []
    listing = (
        psutil.process_iter(["pid", "ppid", "create_time"]) if processes is None else processes
    )
    for process in listing:
        try:
            if process.ppid() != pid:
                continue
            created = float(process.create_time())
        except psutil.Error:
            continue
        if created_from - 1.0 <= created <= created_until:
            found.append(process)
    return found


class Family:
    """The tool process and every descendant seen while it ran."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.parent: Any | None
        try:
            self.parent = psutil.Process(pid)
            self.created = float(self.parent.create_time())
        except psutil.Error:
            self.parent = None
            self.created = time.time()
        self._members: dict[tuple[int, float], tuple[Any, ProcessInfo]] = {}
        self._interval = _FIRST_SCAN_S
        self._next_scan = time.monotonic() + self._interval

    def _remember(self, processes: Iterable[Any]) -> None:
        for process in processes:
            try:
                key = (int(process.pid), float(process.create_time()))
            except psutil.Error:
                continue
            if key not in self._members:
                self._members[key] = (process, _info(process))

    def scan(self) -> None:
        """Remember the descendants the tool has right now."""
        if self.parent is None:
            return
        try:
            self._remember(self.parent.children(recursive=True))
        except psutil.Error:
            return  # the tool has ended: nothing new can be learned this way

    def scan_if_due(self) -> None:
        now = time.monotonic()
        if now >= self._next_scan:
            self.scan()
            self._interval = min(self._interval * 2, _MAX_SCAN_INTERVAL_S)
            self._next_scan = now + self._interval

    def scan_orphans(self) -> None:
        """Also remember children that were never seen while the tool was alive."""
        self._remember(orphans_of(self.pid, self.created, time.time()))

    def alive_descendants(self) -> list[tuple[Any, ProcessInfo]]:
        return [member for member in self._members.values() if is_alive(member[0])]


@dataclass(frozen=True, slots=True)
class KillReport:
    killed: tuple[ProcessInfo, ...] = ()
    survivors: tuple[ProcessInfo, ...] = ()


def _kill(process: Any) -> None:
    try:
        process.kill()
    except psutil.Error:
        return  # already gone, or not ours to kill: the survivor check below tells which


def _wait_until_dead(targets: list[tuple[Any, ProcessInfo]], wait_s: float) -> list[ProcessInfo]:
    deadline = time.monotonic() + wait_s
    while True:
        alive = [info for process, info in targets if is_alive(process)]
        if not alive or time.monotonic() >= deadline:
            return alive
        time.sleep(0.02)


def kill_family(family: Family, wait_s: float | None = None) -> KillReport:
    """Kill every descendant of the tool, then the tool, and confirm that none survives.

    Blocking on purpose and free of ``await``: a second cancellation cannot interrupt it.
    It returns as soon as the processes are gone, normally within milliseconds.
    """
    wait_s = KILL_WAIT_S if wait_s is None else wait_s
    targets: dict[int, tuple[Any, ProcessInfo]] = {}
    for _ in range(3):  # the tool may start a child while its children are being killed
        family.scan()
        fresh = [m for m in family.alive_descendants() if id(m[0]) not in targets]
        if not fresh:
            break
        for member in fresh:
            _kill(member[0])
            targets[id(member[0])] = member
    if family.parent is not None and is_alive(family.parent):
        _kill(family.parent)
        targets[id(family.parent)] = (family.parent, _info(family.parent))
    family.scan_orphans()
    for member in family.alive_descendants():
        if id(member[0]) not in targets:
            _kill(member[0])
            targets[id(member[0])] = member
    members = list(targets.values())
    survivors = _wait_until_dead(members, wait_s)
    return KillReport(tuple(info for _, info in members), tuple(survivors))


def kill_processes(
    members: list[tuple[Any, ProcessInfo]], wait_s: float | None = None
) -> KillReport:
    """Kill the given processes and confirm it."""
    for process, _ in members:
        _kill(process)
    survivors = _wait_until_dead(members, KILL_WAIT_S if wait_s is None else wait_s)
    return KillReport(tuple(info for _, info in members), tuple(survivors))


# --------------------------------------------------------------------------- the gate


class ToolGate:
    """Tools never overlap: one run at a time, first come first served.

    Process-wide and independent of the event loop, so it also holds when two engines
    (two loops) live in one process. Waiting is cancellable.
    """

    def __init__(self) -> None:
        self._mutex = threading.Lock()
        self._queue: deque[object] = deque()
        self._busy = False

    async def acquire(self, ctx: JobContext) -> None:
        ticket = object()
        with self._mutex:
            self._queue.append(ticket)
        try:
            while True:
                with self._mutex:
                    if not self._busy and self._queue[0] is ticket:
                        self._queue.popleft()
                        self._busy = True
                        return
                ctx.check_cancelled()
                await asyncio.sleep(GATE_POLL_S)
        except BaseException:
            with self._mutex:
                if ticket in self._queue:
                    self._queue.remove(ticket)
            raise

    def release(self) -> None:
        with self._mutex:
            self._busy = False

    @property
    def busy(self) -> bool:
        with self._mutex:
            return self._busy


TOOL_GATE = ToolGate()


# --------------------------------------------------------------------------- request, outcome


@dataclass(frozen=True, slots=True)
class ProcessRequest:
    label: str  # for messages, e.g. "DVCfgCmd (action generate)"
    argv: tuple[str, ...]
    cwd: Path
    env: Mapping[str, str]
    timeout_s: float
    log_file: Path
    rules: ResultRules
    encoding: str = "auto"
    max_kept_lines: int = 2000
    leftover_children: str = "kill"  # or "report"
    header: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProcessOutcome:
    argv: tuple[str, ...]
    exit_code: int | None
    elapsed_s: float
    log_file: Path
    lines_total: int = 0
    tail: tuple[str, ...] = ()
    errors: Matches = field(default_factory=Matches)
    warnings: Matches = field(default_factory=Matches)
    licenses: Matches = field(default_factory=Matches)
    #: Children that were still running after the tool had ended.
    leftover: tuple[ProcessInfo, ...] = ()
    leftover_killed: bool = False
    #: Processes that were still alive after they had been killed.
    survivors: tuple[ProcessInfo, ...] = ()
    pipes_left_open: bool = False
    spawn_error: str | None = None


class _TimedOut(Exception):
    pass


class _Collector:
    """Receives every output line: log file, kept tail, matches, throttled progress."""

    def __init__(self, request: ProcessRequest, log: ToolLog, ctx: JobContext) -> None:
        self._rules = request.rules
        self._log = log
        self._ctx = ctx
        self.tail: deque[str] = deque(maxlen=request.max_kept_lines)
        self.lines_total = 0
        self.errors = Matches()
        self.warnings = Matches()
        self.licenses = Matches()
        self._last_progress = 0.0

    def add(self, stream: str, text: str) -> None:
        line_no = self._log.write(f"[{stream}] {text}")
        self.lines_total += 1
        short = shorten(text)
        self.tail.append(short)
        kind = judge_line(self._rules, text)
        if kind is not None:
            line = LogLine(line_no, short, stream)
            if kind == "license":
                self.licenses.add(line)
            elif kind == "error":
                self.errors.add(line)
            else:
                self.warnings.add(line)
        now = time.monotonic()
        if now - self._last_progress >= PROGRESS_INTERVAL_S:
            self._last_progress = now
            self._ctx.progress(self.lines_total, 0, shorten(text, 300))


async def _pump(
    stream: asyncio.StreamReader | None, name: str, collector: _Collector, encoding: str
) -> None:
    if stream is None:
        return
    decoder = LineDecoder(encoding)
    try:
        while True:
            chunk = await stream.read(READ_CHUNK)
            if not chunk:
                break
            for text in decoder.feed(chunk):
                collector.add(name, text)
    finally:
        for text in decoder.flush():
            collector.add(name, text)


def _raise_pump_errors(pumps: Iterable[asyncio.Task[None]]) -> None:
    for pump in pumps:
        if pump.done() and not pump.cancelled():
            error = pump.exception()
            if error is not None:
                raise error


async def _wait_for_exit(
    wait_task: asyncio.Task[int],
    pumps: list[asyncio.Task[None]],
    family: Family,
    deadline: float,
    ctx: JobContext,
) -> int:
    while True:
        watching = {wait_task, *(pump for pump in pumps if not pump.done())}
        await asyncio.wait(watching, timeout=POLL_S, return_when=asyncio.FIRST_COMPLETED)
        _raise_pump_errors(pumps)
        if wait_task.done():
            return wait_task.result()
        ctx.check_cancelled()
        if time.monotonic() >= deadline:
            raise _TimedOut
        family.scan_if_due()


def _close_transport(process: asyncio.subprocess.Process) -> None:
    """Close the pipes of an ended tool that a surviving child still holds open.

    asyncio has no public call for this; without it the pipe handles stay open until the
    garbage collector finds them. ``getattr`` keeps this harmless if the attribute is
    ever renamed.
    """
    transport = getattr(process, "_transport", None)
    if transport is not None:
        transport.close()


async def _settle(
    process: asyncio.subprocess.Process,
    wait_task: asyncio.Task[int],
    pumps: list[asyncio.Task[None]],
) -> None:
    """Let the exit and the readers finish, then give up on what is left."""
    tasks: list[asyncio.Task[Any]] = [wait_task, *pumps]
    try:
        pending = [task for task in tasks if not task.done()]
        if pending:
            await asyncio.wait(pending, timeout=PIPE_DRAIN_S)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        _close_transport(process)


async def _wait_for_leftover(family: Family) -> list[tuple[Any, ProcessInfo]]:
    """Children still running after the tool ended, once they had a short time to end."""
    family.scan_orphans()
    deadline = time.monotonic() + LEFTOVER_GRACE_S
    while True:
        alive = family.alive_descendants()
        if not alive or time.monotonic() >= deadline:
            return alive
        await asyncio.sleep(0.05)


def _license_error(request: ProcessRequest, licenses: Matches, extra: str = "") -> LicenseError:
    first = licenses.lines[0]
    return LicenseError(
        f"{request.label} reported a license problem{extra}: {first.text!r} "
        f"(line {first.line_no} of {request.log_file}). Check the Vector license (dongle, "
        "license server, network) and run it again.",
        detail=str(request.log_file),
    )


async def run_process(request: ProcessRequest, ctx: JobContext) -> ProcessOutcome:
    """Run one tool to its end and return what happened.

    Raises ``ToolTimeoutError`` on timeout, ``LicenseError`` when the tool printed a
    license problem, ``JobCancelled`` (or asyncio's ``CancelledError``, which the engine
    turns into it) on cancel, and ``ToolRunError`` for anything unexpected. In each of
    these cases the process tree has been killed first. A tool that cannot be started is
    not an exception: the outcome carries ``spawn_error``.
    """
    ctx.check_cancelled()
    await TOOL_GATE.acquire(ctx)
    try:
        return await _run_locked(request, ctx)
    finally:
        TOOL_GATE.release()


async def _run_locked(request: ProcessRequest, ctx: JobContext) -> ProcessOutcome:
    started = time.monotonic()
    try:
        log = ToolLog(request.log_file)
    except OSError as error:
        raise ToolRunError(
            f"The log file {request.log_file} cannot be written ({type(error).__name__}: "
            f"{error}). {request.label} was not started.",
            detail=str(request.log_file),
        ) from error
    try:
        return await _run_logged(request, ctx, log, started)
    finally:
        log.close()


async def _run_logged(
    request: ProcessRequest, ctx: JobContext, log: ToolLog, started: float
) -> ProcessOutcome:
    for line in request.header:
        log.write(f"[porttool] {line}")
    log.write(f"[porttool] command: {subprocess.list2cmdline(list(request.argv))}")
    log.write(f"[porttool] working folder: {request.cwd}")
    log.write(f"[porttool] timeout: {request.timeout_s:g} s")
    collector = _Collector(request, log, ctx)
    ctx.progress(0, 0, f"Starting {request.label}")
    try:
        process = await asyncio.create_subprocess_exec(
            *request.argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(request.cwd),
            env=dict(request.env),
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )
    except (OSError, ValueError) as error:
        reason = f"{type(error).__name__}: {error}"
        log.write(f"[porttool] could not be started: {reason}")
        return ProcessOutcome(
            request.argv, None, time.monotonic() - started, request.log_file, spawn_error=reason
        )

    family = Family(process.pid)
    wait_task: asyncio.Task[int] = asyncio.ensure_future(process.wait())
    pumps: list[asyncio.Task[None]] = [
        asyncio.ensure_future(_pump(process.stdout, "out", collector, request.encoding)),
        asyncio.ensure_future(_pump(process.stderr, "err", collector, request.encoding)),
    ]
    leftover: list[tuple[Any, ProcessInfo]] = []
    leftover_killed = False
    survivors: tuple[ProcessInfo, ...] = ()
    pipes_left_open = False
    try:
        exit_code = await _wait_for_exit(
            wait_task, pumps, family, started + request.timeout_s, ctx
        )
        leftover = await _wait_for_leftover(family)
        if leftover and request.leftover_children == "kill":
            survivors = kill_processes(leftover).survivors
            leftover_killed = True
        pending = [pump for pump in pumps if not pump.done()]
        if pending:
            await asyncio.wait(pending, timeout=PIPE_DRAIN_S)
        _raise_pump_errors(pumps)
        pipes_left_open = any(not pump.done() for pump in pumps)
        await _settle(process, wait_task, pumps)
    except BaseException as error:
        report = kill_family(family)
        try:
            await _settle(process, wait_task, pumps)
        finally:
            elapsed = time.monotonic() - started
            what = (
                f"timeout after {request.timeout_s:g} s"
                if isinstance(error, _TimedOut)
                else "cancelled"
                if isinstance(error, JobCancelled | asyncio.CancelledError)
                else f"{type(error).__name__}: {error}"
            )
            log.write(f"[porttool] stopped after {elapsed:.1f} s: {what}")
            log.write(
                "[porttool] killed: "
                + (", ".join(str(info) for info in report.killed) or "nothing left to kill")
            )
            if report.survivors:
                log.write(
                    "[porttool] STILL RUNNING after the kill: "
                    + ", ".join(str(info) for info in report.survivors)
                )
        still = (
            " These processes could not be stopped: "
            + ", ".join(str(info) for info in report.survivors)
            + "."
            if report.survivors
            else ""
        )
        if isinstance(error, _TimedOut):
            if collector.licenses.total:
                raise _license_error(
                    request,
                    collector.licenses,
                    f" and did not finish within {request.timeout_s:g} s",
                ) from None
            raise ToolTimeoutError(
                f"{request.label} did not finish within {request.timeout_s:g} s and was "
                f"stopped ({len(report.killed)} process(es) killed).{still} "
                f"Log: {request.log_file}",
                detail=str(request.log_file),
            ) from None
        if isinstance(error, PortToolError | asyncio.CancelledError) or not isinstance(
            error, Exception
        ):
            raise
        raise ToolRunError(
            f"{request.label} was stopped because of an unexpected problem "
            f"({type(error).__name__}: {error}).{still} Log: {request.log_file}",
            detail=str(request.log_file),
        ) from error

    elapsed = time.monotonic() - started
    log.write(
        f"[porttool] exit code: {exit_code}; {collector.lines_total} line(s); {elapsed:.1f} s"
    )
    if leftover:
        verb = "were stopped" if leftover_killed else "were left running"
        names = ", ".join(str(info) for _, info in leftover)
        log.write(f"[porttool] still running after the tool ended, {verb}: {names}")
    ctx.progress(
        collector.lines_total,
        collector.lines_total,
        f"{request.label} ended with exit code {exit_code} after {elapsed:.1f} s",
    )
    if collector.licenses.total:
        raise _license_error(request, collector.licenses)
    return ProcessOutcome(
        argv=request.argv,
        exit_code=exit_code,
        elapsed_s=elapsed,
        log_file=request.log_file,
        lines_total=collector.lines_total,
        tail=tuple(collector.tail),
        errors=collector.errors,
        warnings=collector.warnings,
        licenses=collector.licenses,
        leftover=tuple(info for _, info in leftover),
        leftover_killed=leftover_killed,
        survivors=survivors,
        pipes_left_open=pipes_left_open,
    )
