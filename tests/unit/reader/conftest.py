"""Fixtures for the reader tests: a JobContext double and a throw-away repository."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any, TypeVar

import pytest

from porttool.core.config import ProjectConfig
from porttool.core.errors import JobCancelled
from porttool.core.models import Model
from porttool.core.results import Finding
from porttool.reader import ArxmlReader, ReaderOptions

_R = TypeVar("_R")


class FakeJobContext:
    """Test double of ``porttool.core.interfaces.JobContext``.

    ``run_blocking`` runs in a real thread pool, progress calls are recorded, and the
    cancel switch can be flipped by hand or automatically after a number of progress
    calls (``cancel_after_progress``) to cancel in the middle of a load.
    """

    def __init__(self, workers: int = 4, cancel_after_progress: int | None = None) -> None:
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="test-worker")
        self._lock = threading.Lock()
        self._cancelled = threading.Event()
        self.cancel_after_progress = cancel_after_progress
        self.events: list[tuple[int, int, str]] = []
        self.blocking_calls = 0
        self.worker_threads: set[str] = set()

    def progress(self, done: int, total: int, message: str = "") -> None:
        with self._lock:
            self.events.append((done, total, message))
            reached = (
                self.cancel_after_progress is not None
                and len(self.events) >= self.cancel_after_progress
            )
        if reached:
            self.cancel()

    def check_cancelled(self) -> None:
        if self._cancelled.is_set():
            raise JobCancelled()

    def cancel(self) -> None:
        self._cancelled.set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    async def run_blocking(self, fn: Callable[[], _R]) -> _R:
        with self._lock:
            self.blocking_calls += 1

        def tracked() -> _R:
            with self._lock:
                self.worker_threads.add(threading.current_thread().name)
            return fn()

        return await asyncio.get_running_loop().run_in_executor(self._pool, tracked)

    def close(self) -> None:
        self._pool.shutdown(wait=True, cancel_futures=True)


class Repo:
    """A repository in a temporary folder, with helpers to write files and load it."""

    def __init__(self, root: Path, cache_dir: Path, large_file_bytes: int | None) -> None:
        self.root = root
        self.cache_dir = cache_dir
        self.large_file_bytes = large_file_bytes
        self.developer = root / "Config" / "Developer"
        self.developer.mkdir(parents=True)
        self.reader = ArxmlReader()
        self.contexts: list[FakeJobContext] = []

    def path(self, rel: str) -> Path:
        return self.root / rel

    def write(self, rel: str, content: str | bytes, encoding: str = "utf-8") -> Path:
        """Write ``content`` to a repo-relative path (bytes are written as they are)."""
        target = self.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_bytes(content.encode(encoding))
        return target

    def config(self, **overrides: Any) -> ProjectConfig:
        base = ProjectConfig(
            repo_root=self.root,
            arxml_roots=(self.developer,),
            cache_dir=self.cache_dir,
        )
        if self.large_file_bytes is not None:
            base = replace(base, large_file_bytes=self.large_file_bytes)
        return replace(base, **overrides) if overrides else base

    def context(self, **kwargs: Any) -> FakeJobContext:
        context = FakeJobContext(**kwargs)
        self.contexts.append(context)
        return context

    def load(
        self,
        config: ProjectConfig | None = None,
        *,
        ctx: FakeJobContext | None = None,
        reader: ArxmlReader | None = None,
        **overrides: Any,
    ) -> Model:
        """Run one load to completion and return the model."""
        chosen = config if config is not None else self.config(**overrides)
        context = ctx if ctx is not None else self.context()
        return asyncio.run((reader or self.reader).load(chosen, context))

    def close(self) -> None:
        for context in self.contexts:
            context.close()


@pytest.fixture
def repo(tmp_path: Path) -> Iterator[Repo]:
    """A repository read with the default strategy (DOM for everything below 50 MB)."""
    instance = Repo(tmp_path / "repo", tmp_path / "cache", None)
    yield instance
    instance.close()


@pytest.fixture(params=["dom", "stream"])
def any_repo(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Repo]:
    """The same repository read once per parsing strategy.

    ``stream`` sets ``large_file_bytes`` to 0 so that every non-empty file goes through
    the streaming path that production uses for files above 50 MB.
    """
    threshold = None if request.param == "dom" else 0
    instance = Repo(tmp_path / "repo", tmp_path / "cache", threshold)
    yield instance
    instance.close()


def codes_of(model: Model) -> list[str]:
    return [f.code for f in model.findings]


def findings_with(model: Model, code: str) -> list[Finding]:
    return [f for f in model.findings if f.code == code]


def no_cache_reader() -> ArxmlReader:
    return ArxmlReader(ReaderOptions(use_cache=False))
