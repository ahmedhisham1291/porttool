"""``ArxmlReader``: the ``porttool.core.interfaces.Reader`` implementation.

The coroutine only orchestrates. Everything that touches the disk, parses or loops over
records runs in the worker pool through ``JobContext.run_blocking``:

1. scan the folders,
2. read the cache (one task),
3. parse every file the cache does not cover (one task per file, largest first),
4. write new cache entries (in batches, so a cancelled load keeps what it parsed),
5. build the model (one task).
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import time
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from porttool.core.config import ProjectConfig
from porttool.core.interfaces import JobContext
from porttool.core.models import Model
from porttool.core.results import Finding, Severity
from porttool.reader import codes
from porttool.reader.build import build_model
from porttool.reader.cache import (
    CacheUnavailable,
    RecordCache,
    cache_version,
    default_cache_dir,
)
from porttool.reader.discovery import DiscoveredFile, Discovery, discover
from porttool.reader.ecuc import EcucLayout
from porttool.reader.parse import ByteBudget, parse_file
from porttool.reader.records import STATUS_OK, FileRecords

_LOG = logging.getLogger(__name__)

#: New cache entries are written when this many are waiting or this much time has passed.
_FLUSH_COUNT = 256
_FLUSH_SECONDS = 2.0
#: Parse tasks handed to the worker pool at the same time (the pool runs as many of
#: them in parallel as it has threads; the rest of the window waits in its queue).
_PARSE_WINDOW = 64


@dataclass(frozen=True, slots=True)
class ReaderOptions:
    """Settings of the reader that are not part of ``ProjectConfig``."""

    use_cache: bool = True
    ecuc_layout: EcucLayout = field(default_factory=EcucLayout)
    #: PENDING-DISCOVERY: how to recognise ARXML that DaVinci Configurator generates for
    #: BSW service components. Components in a file matching one of these patterns
    #: (same syntax as ``ProjectConfig.exclude_globs``) become ``ArxmlOwner.LOCKED``.
    generated_globs: tuple[str, ...] = ("**/ServiceComponents/**",)
    #: Source bytes that may be held as DOM trees at the same time (a tree needs about
    #: five times the file size).
    dom_budget_bytes: int = 128 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class LoadStats:
    """What the last ``load`` did. For the status bar, the benchmark and the hand-off."""

    files_total: int = 0
    files_parsed: int = 0
    files_from_cache: int = 0
    files_failed: int = 0
    bytes_total: int = 0
    bytes_parsed: int = 0
    cache_enabled: bool = False
    schemas: tuple[str, ...] = ()
    seconds_scan: float = 0.0
    seconds_cache_read: float = 0.0
    seconds_parse: float = 0.0
    seconds_cache_write: float = 0.0
    seconds_build: float = 0.0
    seconds_total: float = 0.0


class ArxmlReader:
    """Loads an ECU repository into an immutable ``Model``."""

    def __init__(self, options: ReaderOptions | None = None) -> None:
        self.options = options or ReaderOptions()
        self.last_stats: LoadStats | None = None

    # ---- Reader protocol

    def is_stale(self, config: ProjectConfig, model: Model) -> bool:
        """True if a loaded file changed, appeared or vanished. Stats the files, reads none."""
        current = discover(config, compare_dcf=False).fingerprints()
        loaded = model.files
        if len(current) != len(loaded):
            return True
        for rel_path, (size, mtime_ns) in current.items():
            known = loaded.get(rel_path)
            if known is None or known.size != size or known.mtime_ns != mtime_ns:
                return True
        return False

    async def load(self, config: ProjectConfig, ctx: JobContext) -> Model:
        started = time.perf_counter()
        ctx.check_cancelled()
        ctx.progress(0, 0, "Scanning folders")
        discovery: Discovery = await ctx.run_blocking(partial(discover, config))
        ctx.check_cancelled()
        scanned = time.perf_counter()

        files = discovery.files
        total = len(files) + 1  # the last step is the model build
        findings: list[Finding] = list(discovery.findings)
        records: dict[str, FileRecords] = {}

        cache = self._open_cache(config, findings)
        obsolete: tuple[str, ...] = ()
        if cache is not None:
            wanted = {f.rel_path: (f.size, f.mtime_ns) for f in files}
            try:
                read = await ctx.run_blocking(partial(cache.read, wanted, ctx.check_cancelled))
            except CacheUnavailable as error:
                self._report_cache_failure(findings, cache, error)
                cache = None
            else:
                records.update(read.hits)
                findings.extend(read.findings)
                obsolete = read.obsolete
        ctx.check_cancelled()
        cache_read = time.perf_counter()

        from_cache = len(records)
        to_parse = [f for f in files if f.rel_path not in records]
        ctx.progress(from_cache, total, f"{from_cache} of {len(files)} files unchanged")
        cache, write_seconds = await self._parse(
            config, ctx, to_parse, records, cache, obsolete, findings, from_cache, total
        )
        parsed = time.perf_counter()

        ctx.progress(len(files), total, "Building the model")
        model: Model = await ctx.run_blocking(
            partial(
                build_model,
                config,
                files,
                records,
                extra_files=discovery.extra,
                findings=findings,
                layout=self.options.ecuc_layout,
                generated_globs=self.options.generated_globs,
                check_cancelled=ctx.check_cancelled,
            )
        )
        ctx.check_cancelled()
        ctx.progress(total, total, "Model ready")
        finished = time.perf_counter()

        parsed_names = {f.rel_path for f in to_parse}
        self.last_stats = LoadStats(
            files_total=len(files),
            files_parsed=len(to_parse),
            files_from_cache=from_cache,
            files_failed=sum(1 for r in records.values() if r.status != STATUS_OK),
            bytes_total=sum(f.size for f in files),
            bytes_parsed=sum(f.size for f in files if f.rel_path in parsed_names),
            cache_enabled=cache is not None,
            schemas=tuple(sorted({r.schema for r in records.values() if r.schema is not None})),
            seconds_scan=scanned - started,
            seconds_cache_read=cache_read - scanned,
            seconds_parse=parsed - cache_read - write_seconds,
            seconds_cache_write=write_seconds,
            seconds_build=finished - parsed,
            seconds_total=finished - started,
        )
        return model

    # ---- cache

    def _open_cache(self, config: ProjectConfig, findings: list[Finding]) -> RecordCache | None:
        if not self.options.use_cache:
            return None
        directory: Path | None = config.cache_dir
        if directory is None:
            directory = default_cache_dir(config.repo_root)
        if directory is None:
            findings.append(
                Finding(
                    code=codes.CACHE_DISABLED,
                    severity=Severity.WARNING,
                    message="No cache folder is configured and LOCALAPPDATA is not set. "
                    "This load ran without cache and will be slow every time.",
                )
            )
            return None
        return RecordCache(directory, cache_version(self.options.ecuc_layout.signature))

    @staticmethod
    def _report_cache_failure(
        findings: list[Finding], cache: RecordCache, error: Exception
    ) -> None:
        _LOG.warning("Record cache disabled for this load (%s): %s", cache.directory, error)
        findings.append(
            Finding(
                code=codes.CACHE_DISABLED,
                severity=Severity.WARNING,
                message=f"The cache folder {cache.directory} cannot be used ({error}). "
                "This load ran without cache.",
                file=str(cache.directory),
            )
        )

    # ---- parsing

    async def _parse(
        self,
        config: ProjectConfig,
        ctx: JobContext,
        to_parse: list[DiscoveredFile],
        records: dict[str, FileRecords],
        cache: RecordCache | None,
        obsolete: tuple[str, ...],
        findings: list[Finding],
        done: int,
        total: int,
    ) -> tuple[RecordCache | None, float]:
        """Parse ``to_parse`` into ``records``. Returns the cache (None if it failed) and
        the seconds spent writing it."""
        write_seconds = 0.0
        unsaved: list[FileRecords] = []

        async def flush(delete: tuple[str, ...] = ()) -> None:
            nonlocal cache, write_seconds
            if cache is None or not (unsaved or delete):
                unsaved.clear()
                return
            batch = [r for r in unsaved if r.cacheable]
            unsaved.clear()
            before = time.perf_counter()
            try:
                await ctx.run_blocking(partial(cache.write, batch, delete))
            except CacheUnavailable as error:
                self._report_cache_failure(findings, cache, error)
                cache = None
            write_seconds += time.perf_counter() - before

        if to_parse:
            budget = ByteBudget(self.options.dom_budget_bytes)
            suffixes = self.options.ecuc_layout.container_suffixes
            # Largest first: the big files then overlap with many small ones instead of
            # being left alone at the end.
            waiting = iter(sorted(to_parse, key=lambda f: f.size, reverse=True))
            running: set[asyncio.Future[FileRecords]] = set()

            def submit() -> None:
                # Only a window of tasks is handed to the pool at a time. With every file
                # queued up front, a cache write or any other job would wait behind all
                # of them, and a cancel would have thousands of queued tasks to unwind.
                for found in itertools.islice(waiting, _PARSE_WINDOW - len(running)):
                    running.add(
                        asyncio.ensure_future(
                            ctx.run_blocking(
                                partial(
                                    parse_file,
                                    found.abs_path,
                                    found.rel_path,
                                    config.large_file_bytes,
                                    suffixes,
                                    ctx.check_cancelled,
                                    budget,
                                )
                            )
                        )
                    )

            last_flush = time.perf_counter()
            try:
                submit()
                while running:
                    finished, running = await asyncio.wait(
                        running, return_when=asyncio.FIRST_COMPLETED
                    )
                    for task in finished:
                        result = task.result()
                        records[result.rel_path] = result
                        unsaved.append(result)
                        done += 1
                        ctx.progress(done, total, result.rel_path)
                    ctx.check_cancelled()
                    submit()
                    now = time.perf_counter()
                    if len(unsaved) >= _FLUSH_COUNT or now - last_flush >= _FLUSH_SECONDS:
                        await flush()
                        last_flush = time.perf_counter()
            except BaseException:
                # Cancelled or failed: stop what has not started and collect the rest so
                # that no task is left with an unretrieved exception.
                for task in running:
                    task.cancel()
                await asyncio.gather(*running, return_exceptions=True)
                raise
        await flush(obsolete)
        return cache, write_seconds
