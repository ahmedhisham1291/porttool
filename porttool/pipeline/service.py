"""``PortToolService``: the one object the command line and the GUI talk to.

Implements ``porttool.core.interfaces.AppService``. Thread-safe: every public method may
be called from any thread (HTTP handlers, the CLI). No method does long work; each
``start_*`` submits an engine job and returns its id.
"""

from __future__ import annotations

import importlib
import logging
import os
import threading
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import porttool
from porttool.core.config import ProjectConfig
from porttool.core.config_loader import load_config
from porttool.core.errors import GitError, JobCancelled, PortToolError
from porttool.core.fsutil import local_app_dir, repo_id
from porttool.core.interfaces import (
    ApplyReport,
    DaVinciRunner,
    JobContext,
    JobView,
    Reader,
    ValidationOutcome,
    Validator,
    WorkbookIO,
    WritePlan,
    Writer,
)
from porttool.core.models import Model
from porttool.core.operations import Route
from porttool.core.results import Finding, RawRequestRow, Severity
from porttool.core.sheets import LAYOUT_VERSION, WorkbookMeta
from porttool.engine import Engine, JobHandle
from porttool.pipeline import codes
from porttool.pipeline.dryrun import DryRun, all_findings, count_by_status, run_dry_run
from porttool.pipeline.errors import PipelineError, RecoveryNeededError
from porttool.pipeline.gitrepo import Git
from porttool.pipeline.jobs import JobLog, LoggingContext, count_by_severity, has_errors
from porttool.pipeline.pendingio import read_pending
from porttool.pipeline.settings import PipelineSettings, read_settings

_logger = logging.getLogger(__name__)

#: ``step_hook(step, phase)`` with phase "start" or "end". Used by tests for fault injection.
StepHook = Callable[[str, str], None]
RunnerFactory = Callable[[ProjectConfig], DaVinciRunner]
GuiFindings = Callable[[ProjectConfig], Sequence[Finding]]
_Body = Callable[[LoggingContext], Awaitable[Mapping[str, object]]]

_KEEP_FINISHED_JOBS = 200
_STALE_TTL_S = 2.0
_SHUTDOWN_TIMEOUT_S = 10.0


def _import_attribute(module: str, name: str) -> object:
    """Import ``module.name`` lazily; a missing package becomes a clear PortToolError."""
    try:
        return getattr(importlib.import_module(module), name)
    except (ImportError, AttributeError) as error:
        raise PipelineError(
            f"{module}.{name} is not available in this installation: {error}",
            detail=repr(error),
        ) from error


def _default_reader() -> Reader:
    return cast(
        Reader, cast(Callable[[], object], _import_attribute("porttool.reader", "ArxmlReader"))()
    )


def _default_workbook() -> WorkbookIO:
    factory = cast(Callable[[], object], _import_attribute("porttool.workbook", "XlsxWorkbookIO"))
    return cast(WorkbookIO, factory())


def _default_validator() -> Validator:
    factory = cast(Callable[[], object], _import_attribute("porttool.validator", "ChangeValidator"))
    return cast(Validator, factory())


def _default_writer() -> Writer:
    factory = cast(Callable[[], object], _import_attribute("porttool.writer", "ArxmlWriter"))
    return cast(Writer, factory())


def _default_runner(config: ProjectConfig) -> DaVinciRunner:
    factory = cast(
        Callable[[ProjectConfig], object],
        _import_attribute("porttool.davinci", "DaVinciToolRunner"),
    )
    return cast(DaVinciRunner, factory(config))


def _default_gui_findings(config: ProjectConfig) -> Sequence[Finding]:
    function = cast(
        Callable[[ProjectConfig], Sequence[Finding]],
        _import_attribute("porttool.davinci", "davinci_gui_findings"),
    )
    return function(config)


@dataclass(slots=True)
class _JobRecord:
    name: str
    log: JobLog
    repo_changing: bool = False
    handle: JobHandle[Mapping[str, object]] | None = None
    summary: Mapping[str, object] | None = None
    error: str | None = None


class PortToolService:
    """See the module docstring. Build it with :func:`build_service`."""

    def __init__(
        self,
        engine: Engine,
        *,
        reader: Reader | None = None,
        workbook: WorkbookIO | None = None,
        validator: Validator | None = None,
        writer: Writer | None = None,
        runner_factory: RunnerFactory | None = None,
        gui_findings: GuiFindings | None = None,
        journal_dir: Path | None = None,
        lock_dir: Path | None = None,
        now: Callable[[], datetime] | None = None,
        step_hook: StepHook | None = None,
        git_env: Mapping[str, str] | None = None,
    ) -> None:
        self._engine = engine
        self._reader = reader
        self._workbook = workbook
        self._validator = validator
        self._writer = writer
        self._runner_factory: RunnerFactory = runner_factory or _default_runner
        self._gui_findings: GuiFindings = gui_findings or _default_gui_findings
        self._journal_base = journal_dir
        self._lock_dir = lock_dir
        self._now: Callable[[], datetime] = now or datetime.now
        self._step_hook = step_hook
        self._git_env = dict(git_env) if git_env is not None else None
        #: One thread for restorations: they run even while their job is being cancelled.
        self._rollback_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="porttool-rb")

        self._lock = threading.RLock()
        self._generation = 0
        self._config: ProjectConfig | None = None
        self._settings = PipelineSettings()
        self._settings_findings: tuple[Finding, ...] = ()
        self._model: Model | None = None
        self._dry_run: DryRun | None = None
        self._dry_run_applied = False
        self._validation: ValidationOutcome | None = None
        self._plan: WritePlan | None = None
        self._report: ApplyReport | None = None
        self._jobs: OrderedDict[str, _JobRecord] = OrderedDict()
        self._stale_cache: tuple[int, float, bool] | None = None
        self._closed = False

    # ------------------------------------------------------------------ components

    def _get_reader(self) -> Reader:
        with self._lock:
            if self._reader is None:
                self._reader = _default_reader()
            return self._reader

    def _get_workbook(self) -> WorkbookIO:
        with self._lock:
            if self._workbook is None:
                self._workbook = _default_workbook()
            return self._workbook

    def _get_validator(self) -> Validator:
        with self._lock:
            if self._validator is None:
                self._validator = _default_validator()
            return self._validator

    def _get_writer(self) -> Writer:
        with self._lock:
            if self._writer is None:
                self._writer = _default_writer()
            return self._writer

    def git_for(self, config: ProjectConfig) -> Git:
        return Git(config.repo_root, timeout_s=config.timeouts.git_s, env=self._git_env)

    def journal_folder(self, config: ProjectConfig, settings: PipelineSettings) -> Path:
        r"""``<journals>/<repo id>``: default ``%LOCALAPPDATA%\PortTool\journals\<repo id>``."""
        if self._journal_base is not None:
            base = self._journal_base
        elif settings.journal_dir:
            base = Path(settings.journal_dir)
        else:
            base = local_app_dir() / "journals"
        return base / repo_id(config.repo_root)

    # ------------------------------------------------------------------ AppService: state

    def open_repo(self, repo_root: Path, config_file: Path | None = None) -> tuple[Finding, ...]:
        with self._lock:
            self._refuse_while_repo_job("open a repository")
        loaded = load_config(repo_root, config_file=config_file)
        settings, settings_findings = read_settings(loaded.config)
        with self._lock:
            self._refuse_while_repo_job("open a repository")
            self._generation += 1
            self._config = loaded.config
            self._settings = settings
            self._settings_findings = settings_findings
            self._model = None
            self._dry_run = None
            self._dry_run_applied = False
            self._validation = None
            self._plan = None
            self._report = None
            self._stale_cache = None
        findings = [*loaded.findings, *settings_findings]
        if self.recovery_needed():
            findings.append(
                Finding(
                    code=codes.REC_NEEDED,
                    severity=Severity.ERROR,
                    message=RecoveryNeededError.default_message,
                )
            )
        return tuple(findings)

    def config(self) -> ProjectConfig | None:
        with self._lock:
            return self._config

    def settings(self) -> PipelineSettings:
        with self._lock:
            return self._settings

    def model(self) -> Model | None:
        with self._lock:
            return self._model

    def model_is_stale(self) -> bool:
        """Did files change since the model was loaded? Cached for two seconds.

        The reader lists folders and stats files (no content is read), so a polling
        client costs at most one such scan every two seconds.
        """
        with self._lock:
            config, model, cache = self._config, self._model, self._stale_cache
        if config is None or model is None:
            return False
        now = time.monotonic()
        if cache is not None and cache[0] == id(model) and now - cache[1] < _STALE_TTL_S:
            return cache[2]
        stale = self._get_reader().is_stale(config, model)
        with self._lock:
            self._stale_cache = (id(model), now, stale)
        return stale

    def recovery_needed(self) -> bool:
        from porttool.pipeline.journal import open_journal_file

        with self._lock:
            config, settings = self._config, self._settings
            if config is None or self._repo_job_running():
                return False
        return open_journal_file(self.journal_folder(config, settings)) is not None

    def last_validation(self) -> ValidationOutcome | None:
        with self._lock:
            return self._validation

    def last_plan(self) -> WritePlan | None:
        with self._lock:
            return self._plan

    def last_report(self) -> ApplyReport | None:
        with self._lock:
            return self._report

    # ------------------------------------------------------------------ guards

    def _repo_job_running(self) -> bool:
        return any(
            record.repo_changing and record.handle is not None and not record.handle.done()
            for record in self._jobs.values()
        )

    def _refuse_while_repo_job(self, what: str) -> None:
        if self._repo_job_running():
            raise PipelineError(
                f"An apply or rollback is running on this repository. Wait until it has "
                f"finished before you {what}."
            )

    def _require(self, what: str, *, allow_recovery: bool = False) -> ProjectConfig:
        """The open configuration, or a PortToolError that says why ``what`` is refused."""
        with self._lock:
            if self._closed:
                raise PipelineError("PortTool is shutting down.")
            config = self._config
            if config is None:
                raise PipelineError(f"Open a repository before you {what}.")
            self._refuse_while_repo_job(what)
        if not allow_recovery and self.recovery_needed():
            raise RecoveryNeededError()
        return config

    # ------------------------------------------------------------------ jobs

    def _submit(self, name: str, body: _Body, *, repo_changing: bool = False) -> str:
        with self._lock:
            record = _JobRecord(
                name=name, log=JobLog(self._settings.log_tail_lines), repo_changing=repo_changing
            )

            async def job(ctx: JobContext) -> Mapping[str, object]:
                wrapped = LoggingContext(ctx, record.log)
                try:
                    summary = await body(wrapped)
                except JobCancelled:
                    record.log.add(f"{name}: cancelled")
                    raise
                except PortToolError as error:
                    record.error = str(error)
                    record.log.add(f"{name} failed: {error}")
                    raise
                except Exception as error:  # boundary: engine job (the engine logs it)
                    record.error = f"Unexpected error: {type(error).__name__}: {error}"
                    record.log.add(f"{name} failed: {record.error}")
                    raise
                record.summary = summary
                return summary

            handle = self._engine.submit(job, name=name)
            record.handle = handle
            self._jobs[handle.id] = record
            finished = [
                key for key, item in self._jobs.items() if item.handle and item.handle.done()
            ]
            for key in finished[: max(0, len(finished) - _KEEP_FINISHED_JOBS)]:
                del self._jobs[key]
            return handle.id

    @staticmethod
    def _view(record: _JobRecord) -> JobView:
        handle = record.handle
        assert handle is not None
        status = handle.status()
        done, total, message = status.progress or (0, 0, "")
        state = status.state.value
        return JobView(
            id=status.id,
            name=record.name,
            state=state,
            done=done,
            total=total,
            message=message,
            log_tail=record.log.tail(),
            error=(record.error or status.error) if state == "failed" else None,
            summary=record.summary if state == "done" else None,
        )

    def job(self, job_id: str) -> JobView | None:
        with self._lock:
            record = self._jobs.get(job_id)
        return self._view(record) if record is not None and record.handle is not None else None

    def jobs(self) -> tuple[JobView, ...]:
        with self._lock:
            records = [record for record in self._jobs.values() if record.handle is not None]
        return tuple(self._view(record) for record in records)

    def job_log(self, job_id: str, since: int = 0) -> tuple[tuple[str, ...], int]:
        """Log lines of a job after the first ``since`` lines, and the next index to ask for."""
        with self._lock:
            record = self._jobs.get(job_id)
        return record.log.since(since) if record is not None else ((), since)

    def wait(self, job_id: str, timeout: float | None = None) -> JobView:
        """Block until the job has finished (for the CLI and tests; never call it in a job)."""
        with self._lock:
            record = self._jobs.get(job_id)
        if record is None or record.handle is None:
            raise PipelineError(f"Unknown job {job_id}.")
        record.handle.exception(timeout)
        return self._view(record)

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            record = self._jobs.get(job_id)
        if record is None or record.handle is None:
            return False
        return record.handle.cancel()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._engine.shutdown(timeout=_SHUTDOWN_TIMEOUT_S)
        # A restoration that has started always runs to its end.
        self._rollback_pool.shutdown(wait=True)

    # ------------------------------------------------------------------ shared job parts

    async def _load(self, ctx: LoggingContext, config: ProjectConfig, generation: int) -> Model:
        ctx.progress(0, 0, "Loading the repository")
        model = await self._get_reader().load(config, ctx)
        with self._lock:
            if generation == self._generation:
                self._model = model
                self._stale_cache = None
        return model

    async def _ensure_model(
        self, ctx: LoggingContext, config: ProjectConfig, generation: int
    ) -> Model:
        """The loaded model; loads first when there is none or files changed on disk."""
        with self._lock:
            model = self._model
        if model is not None:
            reader = self._get_reader()
            current = model
            if not await ctx.run_blocking(lambda: reader.is_stale(config, current)):
                return model
            ctx.log("Files changed on disk: loading the repository again")
        return await self._load(ctx, config, generation)

    def _head_commit(self, config: ProjectConfig) -> str | None:
        """HEAD of the repository, or None without git or without a commit. Blocking."""
        git = self.git_for(config)
        try:
            return git.head_commit() if git.inside_work_tree() else None
        except GitError as error:
            _logger.info("git commit of %s is unknown: %s", config.repo_root, error)
            return None

    def _meta(self, model: Model, commit: str | None) -> WorkbookMeta:
        return WorkbookMeta(
            tool_version=porttool.__version__,
            layout_version=LAYOUT_VERSION,
            autosar_schema=model.autosar_schema,
            baseline_commit=commit,
            model_hash=model.model_hash,
            export_time_utc=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

    # ------------------------------------------------------------------ load, export, doctor

    def start_load(self) -> str:
        config = self._require("load the repository")
        generation = self._generation

        async def body(ctx: LoggingContext) -> Mapping[str, object]:
            started = time.perf_counter()
            model = await self._load(ctx, config, generation)
            return {
                "components": len(model.components),
                "ports": len(model.ports),
                "connectors": len(model.connectors),
                "files": len(model.files),
                "findings": count_by_severity(model.findings),
                "model_hash": model.model_hash,
                "elapsed_s": round(time.perf_counter() - started, 3),
            }

        return self._submit("load", body)

    def start_export(self, target: Path) -> str:
        config = self._require("export a workbook")
        generation = self._generation
        target_file = Path(os.path.abspath(target))

        async def body(ctx: LoggingContext) -> Mapping[str, object]:
            model = await self._ensure_model(ctx, config, generation)
            ctx.progress(1, 2, f"Writing {target_file.name}")
            commit = await ctx.run_blocking(lambda: self._head_commit(config))
            meta = self._meta(model, commit)
            with self._lock:
                results = self._validation.results if self._validation is not None else ()
            workbook = self._get_workbook()
            findings = await ctx.run_blocking(
                lambda: workbook.export(model, config, target_file, meta, results)
            )
            ctx.progress(2, 2, "Export finished")
            return {
                "ok": not has_errors(findings),
                "target": str(target_file),
                "baseline_commit": commit,
                "findings": count_by_severity(findings),
                "messages": [f"{f.severity.value.upper()} {f.code}: {f.message}" for f in findings][
                    :50
                ],
            }

        return self._submit("export", body)

    def start_doctor(self) -> str:
        config = self._require("run the doctor", allow_recovery=True)
        with self._lock:
            settings, settings_findings = self._settings, self._settings_findings

        async def body(ctx: LoggingContext) -> Mapping[str, object]:
            from porttool.pipeline.doctor import run_doctor

            findings = await run_doctor(
                ctx,
                config,
                settings,
                settings_findings,
                git=self.git_for(config),
                runner_factory=self._runner_factory,
                journal_folder=self.journal_folder(config, settings),
            )
            for finding in findings:
                ctx.log(f"{finding.severity.value.upper()} {finding.code}: {finding.message}")
            return {
                "ok": not has_errors(findings),
                "findings": count_by_severity(findings),
                "items": [
                    {"code": f.code, "severity": f.severity.value, "message": f.message}
                    for f in findings[:200]
                ],
            }

        return self._submit("doctor", body)

    # ------------------------------------------------------------------ dry run

    def start_dry_run(self, workbooks: Sequence[Path]) -> str:
        files = tuple(Path(os.path.abspath(path)) for path in workbooks)
        return self._start_dry_run(files, None)

    def start_dry_run_rows(self, rows: Sequence[RawRequestRow]) -> str:
        return self._start_dry_run(None, tuple(rows))

    def _start_dry_run(
        self, files: tuple[Path, ...] | None, given_rows: tuple[RawRequestRow, ...] | None
    ) -> str:
        config = self._require("start a dry run")
        generation = self._generation

        async def body(ctx: LoggingContext) -> Mapping[str, object]:
            model = await self._ensure_model(ctx, config, generation)
            ctx.progress(1, 4, "Reading the pending queue")
            pending = await ctx.run_blocking(lambda: read_pending(config))
            rows: Sequence[RawRequestRow] = given_rows or ()
            import_findings: tuple[Finding, ...] = ()
            if files is not None:
                ctx.progress(2, 4, f"Importing {len(files)} workbook(s)")
                commit = await ctx.run_blocking(lambda: self._head_commit(config))
                meta = self._meta(model, commit)
                workbook = self._get_workbook()
                imported = await ctx.run_blocking(lambda: workbook.import_rows(files, meta))
                rows, import_findings = imported.rows, imported.findings
            ctx.progress(3, 4, f"Validating {len(rows)} request(s)")
            validator, writer = self._get_validator(), self._get_writer()
            now_utc = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            dry_run = await ctx.run_blocking(
                lambda: run_dry_run(
                    model, config, rows, import_findings, pending, validator, writer, now_utc
                )
            )
            with self._lock:
                if generation == self._generation:
                    self._dry_run = dry_run
                    self._dry_run_applied = False
                    self._validation = dry_run.outcome
                    self._plan = dry_run.plan
            for line in dry_run.plan.preview[:100]:
                ctx.log(line)
            ctx.progress(4, 4, "Dry run finished")
            return self._dry_run_summary(dry_run)

        return self._submit("dry-run", body)

    @staticmethod
    def _dry_run_summary(dry_run: DryRun) -> Mapping[str, object]:
        change_set = dry_run.outcome.change_set
        findings = all_findings(dry_run)
        return {
            "requests": len(dry_run.outcome.results),
            "by_status": count_by_status(dry_run.outcome.results),
            "operations": {route.value: len(change_set.routed(route)) for route in Route},
            "planned_files": len(dry_run.plan.files),
            "pending_entries": len(dry_run.outcome.pending.entries),
            "findings": count_by_severity(findings),
            "nothing_to_do": dry_run.nothing_to_do,
            "can_apply": dry_run.pending_usable and not dry_run.blocking_findings,
        }

    # ------------------------------------------------------------------ apply, rollback

    def start_apply(self) -> str:
        config = self._require("start an apply")
        with self._lock:
            generation = self._generation
            settings, settings_findings = self._settings, self._settings_findings
            dry_run = None if self._dry_run_applied else self._dry_run
            model = self._model

        async def body(ctx: LoggingContext) -> Mapping[str, object]:
            from porttool.pipeline.transaction import ApplyTransaction, TransactionDeps

            deps = TransactionDeps(
                config=config,
                settings=settings,
                settings_findings=settings_findings,
                reader=self._get_reader(),
                writer=self._get_writer(),
                runner_factory=self._runner_factory,
                gui_findings=self._gui_findings,
                git=self.git_for(config),
                journal_folder=self.journal_folder(config, settings),
                lock_dir=self._lock_dir,
                now=self._now,
                step_hook=self._step_hook,
                rollback_pool=self._rollback_pool,
            )
            transaction = ApplyTransaction(deps, ctx, dry_run=dry_run, current_model=model)
            try:
                report = await transaction.run()
            finally:
                self._publish_apply(generation, transaction.report, transaction)
            return apply_summary(report, transaction.commit_sha, transaction.skipped_steps)

        return self._submit("apply", body, repo_changing=True)

    def _publish_apply(
        self, generation: int, report: ApplyReport | None, transaction: object
    ) -> None:
        from porttool.pipeline.transaction import ApplyTransaction

        assert isinstance(transaction, ApplyTransaction)
        with self._lock:
            if generation != self._generation or report is None:
                return
            self._report = report
            self._stale_cache = None
            if transaction.new_model is not None:
                self._model = transaction.new_model
            if transaction.consumed and self._dry_run is not None:
                self._dry_run_applied = True
                self._validation = replace(self._dry_run.outcome, results=report.results)

    def start_rollback(self) -> str:
        config = self._require("start a rollback", allow_recovery=True)
        with self._lock:
            generation = self._generation
            settings = self._settings

        async def body(ctx: LoggingContext) -> Mapping[str, object]:
            from porttool.pipeline.transaction import recover

            report = await recover(
                ctx,
                config,
                git=self.git_for(config),
                journal_folder=self.journal_folder(config, settings),
                lock_dir=self._lock_dir,
                rollback_pool=self._rollback_pool,
            )
            with self._lock:
                if generation == self._generation:
                    self._report = report
                    self._stale_cache = None
            return apply_summary(report, None, ())

        return self._submit("rollback", body, repo_changing=True)


def apply_summary(
    report: ApplyReport, commit: str | None, skipped_steps: Sequence[str]
) -> Mapping[str, object]:
    """The small JSON-able summary of an apply or rollback job."""
    errors = [f for f in report.findings if f.severity is Severity.ERROR]
    return {
        "ok": report.ok,
        "branch": report.branch,
        "commit": commit,
        "report": report.report_file,
        "rolled_back": report.rolled_back,
        "by_status": count_by_status(report.results),
        "findings": count_by_severity(report.findings),
        "errors": [f"{f.code}: {f.message}" for f in errors[:20]],
        "skipped_steps": list(skipped_steps),
    }


def build_service(
    *,
    engine: Engine | None = None,
    reader: Reader | None = None,
    workbook: WorkbookIO | None = None,
    validator: Validator | None = None,
    writer: Writer | None = None,
    runner_factory: RunnerFactory | None = None,
    gui_findings: GuiFindings | None = None,
    journal_dir: Path | None = None,
    lock_dir: Path | None = None,
    now: Callable[[], datetime] | None = None,
    step_hook: StepHook | None = None,
    git_env: Mapping[str, str] | None = None,
) -> PortToolService:
    """Create the engine and wire reader, workbook, validator, writer and DaVinci runner.

    Every part can be injected (tests do). Parts that are not given are imported on
    first use under the fixed names of docs/SHARED_CONTEXT.md: ``porttool.reader.ArxmlReader``,
    ``porttool.workbook.XlsxWorkbookIO``, ``porttool.validator.ChangeValidator``,
    ``porttool.writer.ArxmlWriter``, ``porttool.davinci.DaVinciToolRunner(config)`` and
    ``porttool.davinci.davinci_gui_findings(config)``.
    """
    if engine is None:
        engine = Engine()
    engine.start()
    return PortToolService(
        engine,
        reader=reader,
        workbook=workbook,
        validator=validator,
        writer=writer,
        runner_factory=runner_factory,
        gui_findings=gui_findings,
        journal_dir=journal_dir,
        lock_dir=lock_dir,
        now=now,
        step_hook=step_hook,
        git_env=git_env,
    )
