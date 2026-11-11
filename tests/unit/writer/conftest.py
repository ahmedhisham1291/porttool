"""Fixtures of the writer tests: a generated repo, a JobContext double, a journal double."""

from __future__ import annotations

import asyncio
import dataclasses
import threading
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import MappingProxyType
from typing import TypeVar

import pytest

from porttool.core.config import ProjectConfig
from porttool.core.config_loader import load_config
from porttool.core.errors import JobCancelled
from porttool.core.interfaces import WriteOutcome, WritePlan
from porttool.core.models import Model
from porttool.core.operations import AnyOperation, ChangeSet
from porttool.core.results import Finding, Severity
from porttool.reader import load_blocking
from porttool.writer import ArxmlWriter
from tests.fixtures.generator import SMALL, GeneratorSpec, GroundTruth, generate

_R = TypeVar("_R")

CT = "/ComponentTypes"
IF = "/PortInterfaces"
DT = "/DataTypes"
COMPOSITION = f"{CT}/EcuComposition"
WORKSPACE = "Config/Developer"


class FakeContext:
    """``JobContext`` double. ``cancel_at`` cancels at the n-th ``check_cancelled`` call
    made on the event loop thread (the calls between two files of an apply)."""

    def __init__(self, cancel_at_progress: int | None = None) -> None:
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="writer-test")
        self._cancelled = threading.Event()
        self.cancel_at_progress = cancel_at_progress
        self.events: list[tuple[int, int, str]] = []
        self.blocking_calls = 0
        self.loop_thread: int | None = None
        self.work_on_loop_thread = False

    def progress(self, done: int, total: int, message: str = "") -> None:
        self.events.append((done, total, message))
        if self.cancel_at_progress is not None and len(self.events) >= self.cancel_at_progress:
            self._cancelled.set()

    def check_cancelled(self) -> None:
        if self._cancelled.is_set():
            raise JobCancelled()

    def cancel(self) -> None:
        self._cancelled.set()

    async def run_blocking(self, fn: Callable[[], _R]) -> _R:
        self.blocking_calls += 1
        self.loop_thread = threading.get_ident()
        return await asyncio.get_running_loop().run_in_executor(self._pool, fn)

    def close(self) -> None:
        self._pool.shutdown(wait=True)


class FakeJournal:
    """Records (path, action, did the file have its old content when it was recorded)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.entries: list[tuple[str, str]] = []
        self.snapshots: dict[str, bytes | None] = {}

    def record(self, rel_path: str, action: str) -> None:
        self.entries.append((rel_path, action))
        path = self.root / rel_path
        self.snapshots[rel_path] = path.read_bytes() if path.exists() else None

    @property
    def paths(self) -> list[str]:
        return [path for path, _ in self.entries]


def errors(findings: Sequence[Finding]) -> list[Finding]:
    return [f for f in findings if f.severity is Severity.ERROR]


def codes_of(findings: Sequence[Finding]) -> list[str]:
    return [f.code for f in findings]


def is_insertion_only(old: bytes, new: bytes) -> bool:
    """True if ``new`` is ``old`` with bytes inserted (``old`` is a subsequence of ``new``)."""
    rest = iter(new)
    return all(byte in rest for byte in old)


class Repo:
    """A repository on disk with helpers to load, plan and apply."""

    def __init__(self, root: Path, cache: Path, truth: GroundTruth | None = None) -> None:
        self.root = root
        self.cache = cache
        self.truth = truth
        self.extras: dict[str, Mapping[str, object]] = {}
        self.xsd: Path | None = None
        self.writer = ArxmlWriter(backoff_s=0.0)
        self.journal = FakeJournal(root)
        self.model = self.load()

    @property
    def config(self) -> ProjectConfig:
        loaded = load_config(self.root)
        assert not loaded.has_errors, loaded.findings
        extras = {**loaded.config.extras, **self.extras}
        return dataclasses.replace(
            loaded.config,
            cache_dir=self.cache,
            extras=MappingProxyType(extras),
            xsd_file=self.xsd,
        )

    def setting(self, **values: object) -> None:
        self.extras["writer"] = {**self.extras.get("writer", {}), **values}

    def load(self) -> Model:
        model, _ = load_blocking(self.config)
        return model

    def reload(self) -> Model:
        self.model = self.load()
        return self.model

    def path(self, rel_path: str) -> Path:
        return self.root / rel_path

    def read(self, rel_path: str) -> bytes:
        return self.path(rel_path).read_bytes()

    def snapshot(self) -> dict[str, bytes]:
        return {
            path.relative_to(self.root).as_posix(): path.read_bytes()
            for path in sorted(self.root.rglob("*"))
            if path.is_file()
        }

    def plan(self, *operations: AnyOperation) -> WritePlan:
        return self.writer.plan(self.model, self.config, ChangeSet(operations=operations))

    def apply_plan(self, plan: WritePlan, ctx: FakeContext | None = None) -> WriteOutcome:
        context = ctx or FakeContext()
        try:
            return asyncio.run(
                self.writer.apply(plan, self.model, self.config, self.journal, context)
            )
        finally:
            context.close()

    def apply(self, *operations: AnyOperation, ctx: FakeContext | None = None) -> WriteOutcome:
        return self.apply_plan(self.plan(*operations), ctx)


@pytest.fixture
def small(tmp_path: Path) -> Repo:
    truth = generate(tmp_path / "repo", SMALL)
    return Repo(tmp_path / "repo", tmp_path / "cache", truth)


def make_repo(tmp_path: Path, spec: GeneratorSpec) -> Repo:
    truth = generate(tmp_path / "repo", spec)
    return Repo(tmp_path / "repo", tmp_path / "cache", truth)


_KINDS = (
    "components",
    "ports",
    "interfaces",
    "runnables",
    "access_points",
    "triggers",
    "compositions",
    "prototypes",
    "connectors",
    "data_types",
    "base_types",
    "compu_methods",
    "constraints",
    "units",
    "mapping_sets",
    "constants",
)


def facts(model: Model) -> dict[str, object]:
    """Every object of the model by ``kind:path``, without its origin (lines move)."""
    result: dict[str, object] = {}
    for kind in _KINDS:
        for path, item in getattr(model, kind).items():
            result[f"{kind}:{path}"] = dataclasses.replace(item, origin=None)
    for event_path, mapping in model.task_mappings.items():
        result[f"task_mappings:{event_path}"] = mapping
    return result


def added(before: Model, after: Model) -> set[str]:
    """Keys of the objects that are new in ``after``; asserts nothing else changed."""
    old, new = facts(before), facts(after)
    for key, item in old.items():
        assert new.get(key) == item, f"{key} changed"
    return set(new) - set(old)


def assert_only_insertions(before: Mapping[str, bytes], after: Mapping[str, bytes]) -> set[str]:
    """Every file that changed only gained bytes. Returns the changed and the new files."""
    assert set(before) <= set(after)
    changed = {name for name in after if before.get(name) != after[name]}
    for name in changed & set(before):
        assert is_insertion_only(before[name], after[name]), name
    return changed
