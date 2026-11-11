"""Job bookkeeping of the service: bounded log tails and the logging job context."""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from collections.abc import Callable, Mapping
from concurrent.futures import Executor
from typing import TypeVar

from porttool.core.interfaces import JobContext
from porttool.core.results import Finding, Severity

_R = TypeVar("_R")


class JobLog:
    """Thread-safe log of one job. Keeps the newest ``max_lines`` lines."""

    def __init__(self, max_lines: int = 200) -> None:
        self._lines: deque[str] = deque(maxlen=max(1, max_lines))
        self._count = 0
        self._lock = threading.Lock()

    def add(self, line: str) -> None:
        text = line.rstrip()
        if not text:
            return
        with self._lock:
            self._lines.append(text[:2000])
            self._count += 1

    def tail(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._lines)

    def since(self, index: int) -> tuple[tuple[str, ...], int]:
        """Lines added after the first ``index`` lines, and the new index (for polling)."""
        with self._lock:
            kept = len(self._lines)
            first = self._count - kept
            start = max(index, first) - first
            return tuple(list(self._lines)[start:]), self._count


class LoggingContext:
    """A ``JobContext`` that also writes every new progress message into the job log.

    Tool output reaches the log this way too: the DaVinci runner reports its output
    lines through ``progress``.
    """

    def __init__(self, inner: JobContext, log: JobLog) -> None:
        self._inner = inner
        self._log = log
        self._last = ""
        self._lock = threading.Lock()

    def progress(self, done: int, total: int, message: str = "") -> None:
        if message:
            with self._lock:
                is_new = message != self._last
                self._last = message
            if is_new:
                self._log.add(message)
        self._inner.progress(done, total, message)

    def check_cancelled(self) -> None:
        self._inner.check_cancelled()

    async def run_blocking(self, fn: Callable[[], _R]) -> _R:
        return await self._inner.run_blocking(fn)

    def log(self, line: str) -> None:
        self._log.add(line)


async def run_uncancellable(executor: Executor, fn: Callable[[], _R]) -> _R:
    """Run ``fn`` in ``executor`` and wait for it even while the job is being cancelled.

    A rollback must finish once it has started. ``JobContext.run_blocking`` refuses to
    start work for a cancelled job, so restoration runs through this helper instead.
    A cancellation that arrives while waiting is raised after ``fn`` has returned.
    """
    loop = asyncio.get_running_loop()
    future = loop.run_in_executor(executor, fn)
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(future)
            break
        except asyncio.CancelledError:
            if future.done():
                raise
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError()
    return result


def count_by_severity(findings: tuple[Finding, ...] | list[Finding]) -> Mapping[str, int]:
    counts = {severity.value: 0 for severity in Severity}
    for finding in findings:
        counts[finding.severity.value] += 1
    return counts


def has_errors(findings: tuple[Finding, ...] | list[Finding]) -> bool:
    return any(finding.severity is Severity.ERROR for finding in findings)
