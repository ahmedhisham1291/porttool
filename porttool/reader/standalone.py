"""Run the reader without the engine: a small thread-pool ``JobContext`` and ``load_blocking``.

The engine (porttool/engine) owns jobs, progress delivery and cancellation in the
application. This module exists so that the reader's command line, its benchmark and
scripts can load a repository on their own. It adds no behaviour to the reader.
"""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

from porttool.core.config import ProjectConfig
from porttool.core.errors import JobCancelled
from porttool.core.models import Model
from porttool.reader.reader import ArxmlReader, LoadStats, ReaderOptions

_R = TypeVar("_R")

ProgressCallback = Callable[[int, int, str], None]


def default_workers() -> int:
    """Parsing releases the GIL, so one worker per core pays off; more only costs memory."""
    return max(2, min(32, os.cpu_count() or 4))


class PoolJobContext:
    """``JobContext`` backed by its own ``ThreadPoolExecutor``. Close it after use."""

    def __init__(self, workers: int | None = None, on_progress: ProgressCallback | None = None):
        self._pool = ThreadPoolExecutor(
            max_workers=workers or default_workers(), thread_name_prefix="porttool-reader"
        )
        self._on_progress = on_progress
        self._cancelled = threading.Event()

    def progress(self, done: int, total: int, message: str = "") -> None:
        if self._on_progress is not None:
            self._on_progress(done, total, message)

    def check_cancelled(self) -> None:
        if self._cancelled.is_set():
            raise JobCancelled()

    def cancel(self) -> None:
        self._cancelled.set()

    async def run_blocking(self, fn: Callable[[], _R]) -> _R:
        return await asyncio.get_running_loop().run_in_executor(self._pool, fn)

    def close(self) -> None:
        self._pool.shutdown(wait=True, cancel_futures=True)


def load_blocking(
    config: ProjectConfig,
    *,
    options: ReaderOptions | None = None,
    workers: int | None = None,
    on_progress: ProgressCallback | None = None,
) -> tuple[Model, LoadStats]:
    """Load ``config`` and return the model with the statistics of the load.

    Blocks the calling thread and starts its own event loop: do not call it from the
    engine thread or the GUI thread; use ``ArxmlReader.load`` inside an engine job there.
    """
    reader = ArxmlReader(options)
    context = PoolJobContext(workers, on_progress)
    try:
        model = asyncio.run(reader.load(config, context))
    finally:
        context.close()
    stats = reader.last_stats
    assert stats is not None
    return model, stats
