"""Protocols between packages. CONTRACT FILE.

Packages depend on these protocols and on the types in porttool.core, never on each
other's internals. Everything that touches disk or a tool runs inside an engine job
and receives a ``JobContext``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeVar

from porttool.core.config import ProjectConfig
from porttool.core.models import Model
from porttool.core.operations import ChangeSet
from porttool.core.pending import PendingQueue
from porttool.core.results import Finding, RawRequestRow, RequestResult
from porttool.core.sheets import WorkbookMeta

_R = TypeVar("_R")


class JobContext(Protocol):
    """Handed to every job by the engine."""

    def progress(self, done: int, total: int, message: str = "") -> None:
        """Report progress. Cheap; the engine throttles delivery. Safe from any thread."""
        ...

    def check_cancelled(self) -> None:
        """Raise ``JobCancelled`` if the user cancelled. Call it inside loops. Thread-safe."""
        ...

    async def run_blocking(self, fn: Callable[[], _R]) -> _R:
        """Run blocking or CPU-heavy work in the worker thread pool and await its result."""
        ...


# ---- reader


class Reader(Protocol):
    async def load(self, config: ProjectConfig, ctx: JobContext) -> Model:
        """Load the repo into a new immutable Model. Problems in files become Model.findings."""
        ...

    def is_stale(self, config: ProjectConfig, model: Model) -> bool:
        """Cheap check: did any loaded file change, appear or vanish since ``model`` was built?"""
        ...


# ---- workbook


@dataclass(frozen=True, slots=True)
class ImportedWorkbooks:
    rows: tuple[RawRequestRow, ...]
    findings: tuple[Finding, ...]
    metas: tuple[tuple[str, WorkbookMeta | None], ...]  # (file, meta) per imported workbook


class WorkbookIO(Protocol):
    def export(
        self,
        model: Model,
        config: ProjectConfig,
        target: Path,
        meta: WorkbookMeta,
        results: Sequence[RequestResult] = (),
    ) -> tuple[Finding, ...]:
        """Write the workbook atomically. Blocking: call it through ``run_blocking``."""
        ...

    def import_rows(self, files: Sequence[Path], current: WorkbookMeta) -> ImportedWorkbooks:
        """Read request rows from one or more workbooks. Blocking. Judges no AUTOSAR rule."""
        ...


# ---- validator


@dataclass(frozen=True, slots=True)
class ValidationOutcome:
    change_set: ChangeSet
    results: tuple[RequestResult, ...]
    pending: PendingQueue
    findings: tuple[Finding, ...] = ()  # findings not tied to one request (conflicts, pending)


class Validator(Protocol):
    def validate(
        self,
        model: Model,
        config: ProjectConfig,
        rows: Sequence[RawRequestRow],
        pending: PendingQueue,
        now_utc: str,
    ) -> ValidationOutcome:
        """Pure function: no file access, no tool access, no exception for a bad request."""
        ...


# ---- writer


@dataclass(frozen=True, slots=True)
class PlannedFile:
    rel_path: str
    action: str  # "create" or "modify"
    summary: str


@dataclass(frozen=True, slots=True)
class WritePlan:
    change_set: ChangeSet
    files: tuple[PlannedFile, ...]
    preview: tuple[str, ...]  # human-readable lines for the dry run
    findings: tuple[Finding, ...] = ()


@dataclass(frozen=True, slots=True)
class WriteOutcome:
    written: tuple[str, ...]  # repo-relative paths changed or created
    applied_operation_ids: tuple[str, ...]
    findings: tuple[Finding, ...] = ()


class Journal(Protocol):
    """Pipeline journal. The writer records every path BEFORE it touches it."""

    def record(self, rel_path: str, action: str) -> None: ...


class Writer(Protocol):
    def plan(self, model: Model, config: ProjectConfig, change_set: ChangeSet) -> WritePlan:
        """Compute what would change. Writes nothing."""
        ...

    async def apply(
        self,
        plan: WritePlan,
        model: Model,
        config: ProjectConfig,
        journal: Journal,
        ctx: JobContext,
    ) -> WriteOutcome:
        """Edit the workspace files in place, atomically, after a fingerprint check."""
        ...

    def build_delta(
        self, model: Model, config: ProjectConfig, change_set: ChangeSet, target: Path
    ) -> tuple[Finding, ...]:
        """Write one delta ARXML with complete elements (fallback import, model packages)."""
        ...


# ---- DaVinci runner


@dataclass(frozen=True, slots=True)
class ToolResult:
    tool: str
    ok: bool
    exit_code: int | None
    elapsed_s: float
    log_file: str | None = None
    findings: tuple[Finding, ...] = ()


class DaVinciRunner(Protocol):
    async def doctor(self, ctx: JobContext) -> tuple[Finding, ...]: ...

    async def check_workspace(self, ctx: JobContext) -> ToolResult: ...

    async def import_arxml(self, arxml: Path, ctx: JobContext) -> ToolResult: ...

    async def run_script_task(
        self, request_json: Path, result_json: Path, ctx: JobContext
    ) -> ToolResult:
        """Map new triggers to tasks and apply preferred solving actions through DVCfgCmd."""
        ...

    async def validate_project(self, ctx: JobContext) -> ToolResult: ...

    async def generate(self, modules: Sequence[str], ctx: JobContext) -> ToolResult: ...

    async def generate_contract_headers(
        self, swc_names: Sequence[str], ctx: JobContext
    ) -> ToolResult: ...


# ---- pipeline


@dataclass(frozen=True, slots=True)
class ApplyReport:
    ok: bool
    branch: str | None
    results: tuple[RequestResult, ...]
    findings: tuple[Finding, ...]
    report_file: str | None = None
    rolled_back: bool = False


class Pipeline(Protocol):
    async def dry_run(self, files: Sequence[Path], ctx: JobContext) -> ValidationOutcome: ...

    async def apply(self, change_set: ChangeSet, ctx: JobContext) -> ApplyReport: ...

    async def rollback(self, ctx: JobContext) -> ApplyReport: ...
