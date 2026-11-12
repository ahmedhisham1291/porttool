"""The apply transaction and the recovery of a crashed apply.

Steps, in order (each is reported through ``ctx.progress``, the job log and the journal):

``preflight`` -> ``branch`` (open the journal, create the branch) -> ``pending`` ->
``write`` -> ``check`` / ``script`` / ``validate`` / ``generate`` (DaVinci, only when
enabled) -> ``verify`` -> ``packages`` -> ``report`` -> ``commit`` -> ``close``.

Until the commit exists, any failure or cancel restores every journaled path, deletes
the new branch and returns to the original branch. After the commit nothing is undone.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from concurrent.futures import Executor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

import porttool
from porttool.core.config import ProjectConfig
from porttool.core.errors import JobCancelled, PortToolError
from porttool.core.fsutil import RepoLock, RepoLockedError, atomic_write_text
from porttool.core.interfaces import (
    ApplyReport,
    DaVinciRunner,
    Reader,
    ToolResult,
    WriteOutcome,
    Writer,
)
from porttool.core.models import Model
from porttool.core.operations import AddTrigger, AnyOperation, Route
from porttool.core.results import Finding, RequestResult, RequestStatus, Severity
from porttool.pipeline import codes
from porttool.pipeline.describe import operation_text, source_text
from porttool.pipeline.dryrun import DryRun, apply_change_set
from porttool.pipeline.errors import ApplyFailedError
from porttool.pipeline.gitrepo import IDENTITY_HELP, Git, GitMissingError, is_identity_problem
from porttool.pipeline.jobs import LoggingContext, has_errors, run_uncancellable
from porttool.pipeline.journal import ApplyJournal, open_journal_file, read_journal
from porttool.pipeline.packages import Package, write_packages
from porttool.pipeline.pendingio import read_pending, render_pending
from porttool.pipeline.report import ReportData, StepTiming, render_report
from porttool.pipeline.restore import (
    RestoreResult,
    close_rolled_back,
    done_finding,
    restore_records,
    rollback_from_journal,
)
from porttool.pipeline.settings import DAVINCI_STEPS, PipelineSettings
from porttool.pipeline.verify import find_trigger, verify_model

STEPS: tuple[str, ...] = (
    "preflight",
    "branch",
    "pending",
    "write",
    "check",
    "script",
    "validate",
    "generate",
    "verify",
    "packages",
    "report",
    "commit",
    "close",
)
REPORT_NAME = "report.html"
ENABLE_HINT = (
    "To run them, list them in porttool.toml: [pipeline] davinci_steps = "
    '["check", "script", "validate", "generate"]. Enable them one by one after '
    "'porttool davinci doctor' and 'porttool discover run' confirmed the command lines."
)
_MAX_LISTED = 10
_LOG_TAIL_LINES = 40
_LOG_TAIL_BYTES = 8000


@dataclass(frozen=True, slots=True)
class TransactionDeps:
    config: ProjectConfig
    settings: PipelineSettings
    settings_findings: tuple[Finding, ...]
    reader: Reader
    writer: Writer
    runner_factory: Callable[[ProjectConfig], DaVinciRunner]
    gui_findings: Callable[[ProjectConfig], Sequence[Finding]]
    git: Git
    journal_folder: Path
    lock_dir: Path | None
    now: Callable[[], datetime]
    step_hook: Callable[[str, str], None] | None
    rollback_pool: Executor


class _StepFailed(Exception):
    """An expected failure of a step: the findings say why. The apply is rolled back."""

    def __init__(self, findings: Sequence[Finding]) -> None:
        super().__init__(findings[0].message if findings else "step failed")
        self.findings = tuple(findings)


@dataclass(slots=True)
class _Preflight:
    findings: list[Finding] = field(default_factory=list)
    nothing_to_do: bool = False
    branch: str | None = None
    commit: str = ""
    untracked: tuple[str, ...] = ()
    tracked: frozenset[str] = frozenset()


def _error(code: str, message: str, file: str | None = None) -> Finding:
    return Finding(code=code, severity=Severity.ERROR, message=message, file=file)


def _listed(paths: Sequence[str]) -> str:
    more = f" and {len(paths) - _MAX_LISTED} more" if len(paths) > _MAX_LISTED else ""
    return ", ".join(paths[:_MAX_LISTED]) + more


def next_branch_name(taken: frozenset[str], today: datetime) -> str:
    """``porttool/<yyyymmdd>-<n>`` with the next free number of that day."""
    prefix = f"porttool/{today.strftime('%Y%m%d')}-"
    numbers = [
        int(name[len(prefix) :])
        for name in taken
        if name.startswith(prefix) and name[len(prefix) :].isdigit()
    ]
    return f"{prefix}{max(numbers, default=0) + 1}"


def _inside(path: str, scopes: Sequence[str]) -> bool:
    key = os.path.normcase(path)
    for scope in scopes:
        base = os.path.normcase(scope)
        if base in ("", ".") or key == base or key.startswith(base + "/"):
            return True
    return False


def _relative(config: ProjectConfig, path: Path) -> str | None:
    """Repo-relative form of ``path``, "." for the repo folder, None when it lies outside."""
    absolute = Path(os.path.abspath(path))
    root = Path(os.path.abspath(config.repo_root))
    if absolute == root:
        return "."
    return absolute.relative_to(root).as_posix() if absolute.is_relative_to(root) else None


def _tail(file: str) -> str:
    try:
        with open(file, "rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - _LOG_TAIL_BYTES))
            text = stream.read().decode("utf-8", errors="replace")
    except OSError as error:
        return f"(the log file {file} could not be read: {error.strerror or error})"
    return "\n".join(text.splitlines()[-_LOG_TAIL_LINES:])


def _snapshot(folder: Path) -> dict[str, tuple[int, int]]:
    """(size, mtime) of every file below ``folder``: finds what a generation run changed."""
    found: dict[str, tuple[int, int]] = {}
    for base, _dirs, names in os.walk(folder):
        for name in names:
            path = os.path.join(base, name)
            try:
                info = os.stat(path)
            except OSError:
                continue
            found[path] = (info.st_size, info.st_mtime_ns)
    return found


def manual_mapping_instructions(
    runner: object | None, mappings: Sequence[Mapping[str, object]]
) -> tuple[str, ...]:
    """Instructions for mapping new events to tasks by hand.

    The ``DaVinciRunner`` protocol has no method for this (see HANDOFF.md, contract change
    requests). A runner that offers ``manual_mapping_instructions(mappings)`` is asked;
    otherwise a built-in, tool-neutral text is used.
    """
    provider = getattr(runner, "manual_mapping_instructions", None)
    if callable(provider):
        try:
            return tuple(str(line) for line in provider(mappings))
        except (PortToolError, TypeError, ValueError):
            pass  # the built-in text below is always available
    lines = [
        "Open the project (.dpa) in DaVinci Configurator.",
        "Open the RTE task mapping (menu names are UNVERIFIED: Runtime System > Task Mapping).",
    ]
    for item in mappings:
        after = item.get("position_after_event")
        lines.append(
            f"Map event {item.get('event_name') or '(the new event)'} of runnable "
            f"{item.get('runnable')} in {item.get('swc_path')} to task {item.get('task')}"
            + (f", after event {after}." if after else ".")
        )
    lines.append("Save, validate the project and generate RTE and OS.")
    return tuple(lines)


class ApplyTransaction:
    """One apply. Create it, ``await run()``, then read ``report`` and the other results."""

    def __init__(
        self,
        deps: TransactionDeps,
        ctx: LoggingContext,
        *,
        dry_run: DryRun | None,
        current_model: Model | None,
    ) -> None:
        self.deps = deps
        self.ctx = ctx
        self.dry_run = dry_run
        self.current_model = current_model
        # ---- results read by the service
        self.report: ApplyReport | None = None
        self.new_model: Model | None = None
        self.consumed = False
        self.commit_sha: str | None = None
        self.skipped_steps: tuple[str, ...] = ()
        # ---- state of the run
        self.findings: list[Finding] = []
        self.timings: list[StepTiming] = []
        self.current_step = ""
        self.journal: ApplyJournal | None = None
        self.branch: str | None = None
        self.original_branch: str | None = None
        self.original_commit: str | None = None
        self.untracked_before: tuple[str, ...] = ()
        self.committed = False
        self.closed = False
        self.runner: DaVinciRunner | None = None
        self.write_outcome: WriteOutcome | None = None
        self.verified_model: Model | None = None
        self.tool_logs: list[tuple[str, str]] = []
        self.trigger_mappings: list[dict[str, object]] = []
        self.mapping_done = False
        self.manual_instructions: tuple[str, ...] = ()
        self.generated_files: tuple[str, ...] = ()
        self.changed_files: tuple[str, ...] = ()
        self.diff_stat = ""
        self.packages: tuple[Package, ...] = ()
        self.report_file: Path | None = None
        self.final_results: tuple[RequestResult, ...] = ()

    # ------------------------------------------------------------------ small helpers

    @property
    def _results(self) -> tuple[RequestResult, ...]:
        return self.dry_run.outcome.results if self.dry_run is not None else ()

    def _hook(self, step: str, phase: str) -> None:
        if self.deps.step_hook is not None:
            self.deps.step_hook(step, phase)

    def _skip(self, step: str, note: str) -> None:
        self.timings.append(StepTiming(step, "skipped", 0.0, note))
        self.ctx.log(f"{step}: skipped ({note})")

    @asynccontextmanager
    async def _step(self, name: str, title: str) -> AsyncIterator[None]:
        self.ctx.check_cancelled()
        self.current_step = name
        self.ctx.progress(STEPS.index(name), len(STEPS), title)
        journal = self.journal
        if journal is not None:
            journal.current_step = name
            await self.ctx.run_blocking(lambda: journal.step(name, "started"))
        started = time.perf_counter()
        try:
            self._hook(name, "start")
            yield
            self._hook(name, "end")
        except BaseException as error:
            cancelled = isinstance(error, asyncio.CancelledError | JobCancelled)
            self.timings.append(
                StepTiming(
                    name,
                    "cancelled" if cancelled else "failed",
                    time.perf_counter() - started,
                )
            )
            raise
        self.timings.append(StepTiming(name, "ok", time.perf_counter() - started))
        journal = self.journal
        if journal is not None and not self.closed:
            await self.ctx.run_blocking(lambda: journal.step(name, "finished"))

    def _set_report(self, report: ApplyReport) -> ApplyReport:
        self.report = report
        return report

    # ------------------------------------------------------------------ run

    async def run(self) -> ApplyReport:
        lock = RepoLock(self.deps.config.repo_root, lock_dir=self.deps.lock_dir)
        try:
            try:
                await self.ctx.run_blocking(lock.acquire)
            except RepoLockedError as error:
                self.findings.append(_error(codes.PRE_LOCK, error.user_message))
                return self._set_report(
                    ApplyReport(False, None, self._results, tuple(self.findings))
                )
            return await self._run_locked()
        finally:
            lock.release()

    async def _run_locked(self) -> ApplyReport:
        async with self._step("preflight", "Checking the repository"):
            pre = await self.ctx.run_blocking(self._preflight)
        self.findings.extend(pre.findings)
        for finding in pre.findings:
            if finding.severity is not Severity.INFO:
                self.ctx.log(f"{finding.severity.value.upper()} {finding.code}: {finding.message}")
        if has_errors(pre.findings):
            self.ctx.log("The apply was refused. Nothing was changed.")
            return self._set_report(ApplyReport(False, None, self._results, tuple(self.findings)))
        if pre.nothing_to_do:
            message = "Nothing to apply: the dry run holds no change. No branch was created."
            self.findings.append(Finding(codes.APPLY_NOTHING, Severity.INFO, message))
            self.ctx.log(message)
            return self._set_report(ApplyReport(True, None, self._results, tuple(self.findings)))
        try:
            await self._forward(pre)
        except BaseException as error:  # noqa: BLE001 - every failure and cancel is rolled back
            return await self._on_failure(error)
        assert self.report is not None
        return self.report

    # ------------------------------------------------------------------ a. preflight

    def _workspace_scopes(self) -> tuple[str, ...]:
        config = self.deps.config
        roots = list(config.arxml_roots)
        if config.dcf_file is not None:
            roots.append(config.dcf_file.parent)
        return tuple(rel for rel in (_relative(config, root) for root in roots) if rel is not None)

    def _preflight(self) -> _Preflight:
        deps, config, settings = self.deps, self.deps.config, self.deps.settings
        pre = _Preflight(findings=list(deps.settings_findings))
        add = pre.findings.append
        if open_journal_file(deps.journal_folder) is not None:
            add(
                _error(
                    codes.PRE_JOURNAL,
                    "An earlier apply did not finish and left a journal. Roll it back first "
                    "('porttool rollback').",
                )
            )
        dry = self.dry_run
        if dry is None:
            add(_error(codes.PRE_NO_DRY_RUN, "There is no dry run to apply. Run a dry run first."))
            return pre
        if dry.model is not self.current_model:
            add(
                _error(
                    codes.PRE_MODEL_CHANGED,
                    "The repository was loaded again after the dry run. Run the dry run again.",
                )
            )
        elif deps.reader.is_stale(config, dry.model):
            add(
                _error(
                    codes.PRE_STALE,
                    "Files changed on disk since the dry run. Load the repository and run "
                    "the dry run again.",
                )
            )
        blocking = dry.blocking_findings
        if blocking:
            pre.findings.extend(blocking)
            add(
                _error(
                    codes.PRE_PLAN_ERRORS,
                    f"The write plan of the dry run has {len(blocking)} error(s): "
                    f"{blocking[0].message}",
                )
            )
        pending = read_pending(config)
        if not pending.usable or not dry.pending_usable:
            pre.findings.extend(pending.findings)
            add(
                _error(
                    codes.PRE_PENDING,
                    f"The pending file {config.pending_file} cannot be used, so the apply is "
                    "refused: it would overwrite the queue. Repair the file and run the dry "
                    "run again.",
                    config.pending_file,
                )
            )
        elif pending.queue != dry.pending_before:
            add(
                _error(
                    codes.PRE_PENDING,
                    f"The pending file {config.pending_file} changed since the dry run. Run "
                    "the dry run again.",
                    config.pending_file,
                )
            )
        operations = dry.outcome.change_set.operations
        deletes = [op for op in operations if op.op.startswith("delete_")]
        if deletes and not settings.allow_delete:
            add(
                _error(
                    codes.PRE_DELETE,
                    f"{len(deletes)} delete request(s) are refused: deleting is switched off. "
                    "Set [pipeline] allow_delete = true in porttool.toml to allow it, or "
                    "remove the DELETE rows.",
                )
            )
        if has_errors(pre.findings):
            return pre
        if dry.nothing_to_do:
            pre.nothing_to_do = True
            return pre
        self._preflight_git(pre)
        free = shutil.disk_usage(config.repo_root).free
        if free < config.min_free_disk_bytes:
            add(
                _error(
                    codes.PRE_DISK,
                    f"Only {free // (1024 * 1024)} MB are free on the drive of "
                    f"{config.repo_root}; {config.min_free_disk_bytes // (1024 * 1024)} MB "
                    "are required ([limits] min_free_disk_bytes).",
                )
            )
        if settings.any_davinci_step and dry.outcome.change_set.routed(Route.APPLY):
            self._preflight_davinci(pre)
        return pre

    def _preflight_git(self, pre: _Preflight) -> None:
        git, add = self.deps.git, pre.findings.append
        try:
            if not git.inside_work_tree():
                add(
                    _error(
                        codes.PRE_NOT_A_REPO,
                        f"{git.repo_root} is not inside a git work tree. PortTool writes "
                        "only on a new git branch.",
                    )
                )
                return
        except GitMissingError as error:
            add(_error(codes.PRE_GIT_MISSING, error.user_message))
            return
        head = git.head_commit()
        if head is None:
            add(_error(codes.PRE_NO_COMMIT, "The git repository has no commit yet."))
            return
        pre.commit, pre.branch = head, git.current_branch()
        running = git.in_progress()
        if running:
            add(
                _error(
                    codes.PRE_IN_PROGRESS,
                    f"A {' and a '.join(running)} is in progress in this repository. "
                    "Finish or abort it first.",
                )
            )
        entries = git.status()
        dirty = [entry.path for entry in entries if not entry.untracked]
        untracked = [entry.path for entry in entries if entry.untracked]
        workspace = self._workspace_scopes()
        inside = [path for path in untracked if _inside(path, workspace)]
        outside = [path for path in untracked if not _inside(path, workspace)]
        if dirty:
            add(
                _error(
                    codes.PRE_DIRTY,
                    f"The working tree has {len(dirty)} uncommitted change(s): "
                    f"{_listed(dirty)}. Commit or stash them first.",
                )
            )
        if inside:
            add(
                _error(
                    codes.PRE_UNTRACKED_WORKSPACE,
                    f"{len(inside)} untracked file(s) lie in the workspace folders: "
                    f"{_listed(inside)}. Commit, ignore or remove them first.",
                )
            )
        if outside:
            add(
                Finding(
                    codes.PRE_UNTRACKED_OTHER,
                    Severity.WARNING,
                    f"{len(outside)} untracked file(s) outside the workspace folders are left "
                    f"alone: {_listed(outside)}. Add them to .gitignore to silence this.",
                )
            )
        problem = git.identity_problem()
        if problem is not None:
            add(_error(codes.PRE_IDENTITY, f"{IDENTITY_HELP}\n(git said: {problem.strip()})"))
        pre.untracked = tuple(untracked)
        pre.tracked = git.tracked_files(head)

    def _preflight_davinci(self, pre: _Preflight) -> None:
        deps, add = self.deps, pre.findings.append
        try:
            open_guis = list(deps.gui_findings(deps.config))
            self.runner = deps.runner_factory(deps.config)
        except PortToolError as error:
            add(
                _error(
                    codes.PRE_RUNNER,
                    "DaVinci steps are enabled ([pipeline] davinci_steps) but the DaVinci "
                    f"runner is not available: {error.user_message}",
                )
            )
            return
        if open_guis:
            pre.findings.extend(open_guis)
            add(
                _error(
                    codes.PRE_GUI,
                    "DaVinci Developer or DaVinci Configurator seems to have the project "
                    "open. Close it, then start the apply again.",
                )
            )

    # ------------------------------------------------------------------ b..g forward

    async def _forward(self, pre: _Preflight) -> None:
        deps, ctx = self.deps, self.ctx
        config, settings = deps.config, deps.settings
        dry = self.dry_run
        assert dry is not None
        apply_ops = dry.outcome.change_set.routed(Route.APPLY)
        self.original_branch, self.original_commit = pre.branch, pre.commit
        self.untracked_before = pre.untracked

        async with self._step("branch", "Creating the branch and the journal"):
            await ctx.run_blocking(lambda: self._open_journal_and_branch(pre))
        journal = self.journal
        assert journal is not None and self.branch is not None
        ctx.log(f"Branch {self.branch} created from {pre.branch or 'detached HEAD'}")

        async with self._step("pending", "Updating the pending queue"):
            await ctx.run_blocking(self._write_pending)

        if apply_ops:
            async with self._step("write", f"Writing {len(dry.plan.files)} workspace file(s)"):
                outcome = await deps.writer.apply(dry.plan, dry.model, config, journal, ctx)
                self.write_outcome = outcome
                problems = await ctx.run_blocking(lambda: self._check_write(outcome, apply_ops))
                if problems:
                    raise _StepFailed(problems)
        else:
            note = "no request is applied to the workspace now"
            self._skip("write", note)
            self.findings.append(
                Finding(
                    codes.APPLY_NO_WORKSPACE_WRITE,
                    Severity.INFO,
                    "No workspace file is written: every request waits for a model owner or "
                    "is on hold. The pending queue and the request packages are updated.",
                )
            )

        self.trigger_mappings = [
            {
                "operation_id": op.id,
                "swc_path": op.swc_path,
                "runnable": op.runnable_name,
                "trigger_kind": op.kind.value,
                "port": op.port_name,
                "target": op.target,
                "period_s": op.period_s,
                "task": op.task,
                "position_after_event": op.position_after_event,
                "event_path": None,
                "event_name": None,
            }
            for op in apply_ops
            if isinstance(op, AddTrigger) and op.task is not None
        ]
        skipped = [step for step in DAVINCI_STEPS if not settings.enabled(step)]
        if apply_ops and settings.any_davinci_step:
            await self._davinci(apply_ops)
        else:
            skipped = list(DAVINCI_STEPS)
        for step in skipped:
            self._skip(step, "not enabled" if apply_ops else "nothing was written")
        self.skipped_steps = tuple(skipped) if apply_ops else ()
        if self.skipped_steps:
            self.findings.append(
                Finding(
                    codes.APPLY_STEPS_SKIPPED,
                    Severity.WARNING,
                    f"DaVinci steps skipped: {', '.join(self.skipped_steps)}. The ARXML was "
                    f"edited and verified by reloading it only. {ENABLE_HINT}",
                )
            )

        if apply_ops:
            async with self._step("verify", "Reloading the workspace to verify the result"):
                new_model = await deps.reader.load(config, ctx)
                problems = await ctx.run_blocking(lambda: self._verify(new_model, apply_ops))
                if problems:
                    raise _StepFailed(problems)
                self.verified_model = new_model
        else:
            self._skip("verify", "nothing was written to the workspace")
        if self.trigger_mappings and not self.mapping_done:
            runner = self.runner or await ctx.run_blocking(self._optional_runner)
            self.manual_instructions = manual_mapping_instructions(runner, self.trigger_mappings)
            self.findings.append(
                Finding(
                    codes.DV_MANUAL_MAPPING,
                    Severity.WARNING,
                    f"{len(self.trigger_mappings)} new trigger(s) are not mapped to a task "
                    "yet. The report lists them with instructions for DaVinci Configurator.",
                )
            )

        async with self._step("packages", "Writing request packages for model owners"):
            await ctx.run_blocking(self._write_packages)
        async with self._step("report", "Writing the report"):
            await ctx.run_blocking(self._stage_and_report)
        async with self._step("commit", f"Committing on branch {self.branch}"):
            await ctx.run_blocking(self._commit)
        async with self._step("close", "Closing the journal"):
            await ctx.run_blocking(self._close)
        ctx.progress(len(STEPS), len(STEPS), f"Applied on branch {self.branch}")
        self._success_report()

    def _success_report(self) -> None:
        self.new_model = self.verified_model
        self.consumed = True
        self.report = ApplyReport(
            ok=True,
            branch=self.branch,
            results=self.final_results,
            findings=tuple(self.findings),
            report_file=str(self.report_file) if self.report_file is not None else None,
        )

    # ---- b. branch and journal

    def _open_journal_and_branch(self, pre: _Preflight) -> None:
        deps = self.deps
        name = next_branch_name(deps.git.branches_like("porttool/"), deps.now())
        journal = ApplyJournal.create(
            deps.journal_folder,
            repo_root=deps.config.repo_root,
            original_branch=pre.branch,
            original_commit=pre.commit,
            tracked=pre.tracked,
            tool_version=porttool.__version__,
        )
        self.journal = journal
        journal.current_step = "branch"
        journal.step("branch", "started")
        journal.note("branch", name=name)
        deps.git.create_branch(name)
        self.branch = name

    # ---- c. pending queue

    def _write_pending(self) -> None:
        dry, config, journal = self.dry_run, self.deps.config, self.journal
        assert dry is not None and journal is not None
        if not dry.pending_changed:
            self.ctx.log("The pending queue is unchanged")
            return
        path = config.repo_root / config.pending_file
        journal.record(config.pending_file, "modify" if path.exists() else "create")
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, render_pending(dry.outcome.pending), newline="\n")
        self.ctx.log(
            f"Pending queue written: {len(dry.outcome.pending.entries)} entr(y/ies) in "
            f"{config.pending_file}"
        )

    # ---- d. write

    def _check_write(
        self, outcome: WriteOutcome, apply_ops: Sequence[AnyOperation]
    ) -> list[Finding]:
        journal = self.journal
        assert journal is not None
        self.findings.extend(f for f in outcome.findings if f.severity is not Severity.ERROR)
        problems = [f for f in outcome.findings if f.severity is Severity.ERROR]
        if problems:
            problems.append(
                _error(codes.APPLY_WRITER, f"The writer reported {len(problems)} error(s).")
            )
        late = [path for path in outcome.written if not journal.has(path)]
        for path in late:
            journal.record(path, "late")  # so that the rollback can still restore it
        if late:
            problems.append(
                _error(
                    codes.APPLY_NOT_JOURNALED,
                    f"The writer changed {len(late)} file(s) without journaling them first: "
                    f"{_listed(late)}. This is a defect of the writer.",
                )
            )
        applied = set(outcome.applied_operation_ids)
        missing = [op for op in apply_ops if op.id not in applied]
        if missing and not problems:
            problems.append(
                _error(
                    codes.APPLY_NOT_APPLIED,
                    f"The writer did not apply {len(missing)} planned operation(s): "
                    f"{_listed([operation_text(op) for op in missing])}.",
                )
            )
        return problems

    # ---- e. DaVinci steps

    def _optional_runner(self) -> DaVinciRunner | None:
        try:
            return self.deps.runner_factory(self.deps.config)
        except PortToolError:
            return None  # the built-in manual instructions are used

    def _tool_scopes(self) -> tuple[str, ...]:
        config, settings = self.deps.config, self.deps.settings
        if settings.tool_write_scopes:
            return tuple(
                scope.replace("\\", "/").strip("/") or "." for scope in settings.tool_write_scopes
            )
        # PENDING-DISCOVERY: which files DVWspChecker, DVImEx and DVCfgCmd write is not
        # known yet. Default: the ARXML roots, the folder of the ECUC file, the .dpa file
        # and the generated folder. [pipeline] tool_write_scopes overrides this.
        paths: list[Path] = list(config.arxml_roots)
        if config.ecuc_file is not None:
            paths.append(config.ecuc_file.parent)
        paths += [p for p in (config.dpa_file, config.dcf_file, config.gen_data) if p is not None]
        scopes: list[str] = []
        for path in paths:
            rel = _relative(config, path)
            if rel is not None and rel not in scopes:
                scopes.append(rel)
        return tuple(scopes)

    def _journal_scopes(self) -> None:
        journal = self.journal
        assert journal is not None
        for scope in self._tool_scopes():
            journal.scope(scope, [p for p in self.untracked_before if _inside(p, (scope,))])

    async def _tool_done(self, step: str, result: ToolResult, extra: Path | None = None) -> None:
        self.findings.extend(result.findings)
        state = "ok" if result.ok else f"failed (exit code {result.exit_code})"
        self.ctx.log(f"{result.tool} {step}: {state} in {result.elapsed_s:.1f} s")
        log_file = result.log_file

        def read() -> list[tuple[str, str]]:
            found: list[tuple[str, str]] = []
            if log_file is not None:
                found.append((f"{step}: {result.tool} ({state}), {log_file}", _tail(log_file)))
            if extra is not None and extra.is_file():
                found.append((f"{step}: {extra.name}", _tail(str(extra))))
            return found

        self.tool_logs.extend(await self.ctx.run_blocking(read))

    def _tool_failure(self, code: str, what: str, result: ToolResult) -> _StepFailed:
        log = f" Log: {result.log_file}." if result.log_file else ""
        return _StepFailed(
            [
                *(f for f in result.findings if f.severity is Severity.ERROR),
                _error(code, f"{what} ({result.tool}, exit code {result.exit_code}).{log}"),
            ]
        )

    async def _davinci(self, apply_ops: Sequence[AnyOperation]) -> None:
        deps, ctx, settings = self.deps, self.ctx, self.deps.settings
        config, runner, journal = deps.config, self.runner, self.journal
        assert runner is not None and journal is not None
        dry = self.dry_run
        assert dry is not None
        await ctx.run_blocking(self._journal_scopes)

        if settings.enabled("check"):
            async with self._step("check", "Checking the workspace (DVWspChecker)"):
                result = await runner.check_workspace(ctx)
                await self._tool_done("check", result)
                if not result.ok:
                    if not settings.fallback_import:
                        raise self._tool_failure(
                            codes.DV_CHECK_FAILED, "The workspace check failed", result
                        )
                    await self._fallback_import(runner, result)

        if settings.enabled("script"):
            async with self._step("script", "Mapping triggers to tasks (DVCfgCmd script task)"):
                if self.trigger_mappings:
                    # The script needs the paths of the events the writer created.
                    model = await deps.reader.load(config, ctx)
                    await ctx.run_blocking(lambda: self._resolve_events(model, apply_ops))
                request = journal.work_dir / "script_request.json"
                answer = journal.work_dir / "script_result.json"
                document = {
                    "format_version": 1,
                    "generated_by": f"porttool {porttool.__version__}",
                    "task_mappings": self.trigger_mappings,
                    "apply_solving_actions": True,
                }
                text = json.dumps(document, indent=2, ensure_ascii=False)
                await ctx.run_blocking(lambda: atomic_write_text(request, text + "\n"))
                result = await runner.run_script_task(request, answer, ctx)
                await self._tool_done("script", result, answer)
                if not result.ok:
                    self.manual_instructions = manual_mapping_instructions(
                        runner, self.trigger_mappings
                    )
                    raise self._tool_failure(
                        codes.DV_SCRIPT_FAILED, "The script task failed", result
                    )
                self.mapping_done = True

        if settings.enabled("validate"):
            async with self._step("validate", "Validating the project (DVCfgCmd)"):
                result = await runner.validate_project(ctx)
                await self._tool_done("validate", result)
                if not result.ok:
                    raise self._tool_failure(
                        codes.DV_VALIDATE_FAILED, "The project validation failed", result
                    )

        if settings.enabled("generate"):
            modules = ", ".join(config.generate_modules)
            async with self._step("generate", f"Generating {modules} (DVCfgCmd)"):
                gen_data = config.gen_data
                before = await ctx.run_blocking(lambda: _snapshot(gen_data)) if gen_data else {}
                result = await runner.generate(config.generate_modules, ctx)
                await self._tool_done("generate", result)
                if not result.ok:
                    raise self._tool_failure(codes.DV_GENERATE_FAILED, "Generation failed", result)
                if gen_data is not None:
                    await ctx.run_blocking(lambda: self._generated_changes(gen_data, before))

    def _generated_changes(self, gen_data: Path, before: dict[str, tuple[int, int]]) -> None:
        after = _snapshot(gen_data)
        changed = sorted(
            {path for path, info in after.items() if before.get(path) != info}
            | {path for path in before if path not in after}
        )
        root = self.deps.config.repo_root
        self.generated_files = tuple(
            Path(path).relative_to(root).as_posix() if Path(path).is_relative_to(root) else path
            for path in changed
        )
        rel = _relative(self.deps.config, gen_data)
        if rel is not None and self.deps.git.is_ignored(rel):
            self.findings.append(
                Finding(
                    codes.DV_GENERATED_IGNORED,
                    Severity.INFO,
                    f"{rel} is ignored by git: the {len(changed)} generated file(s) that "
                    "changed are not committed, and a rollback cannot restore them.",
                    file=rel,
                )
            )

    def _resolve_events(self, model: Model, apply_ops: Sequence[AnyOperation]) -> None:
        dry = self.dry_run
        assert dry is not None
        by_id = {op.id: op for op in apply_ops}
        for item in self.trigger_mappings:
            op = by_id.get(str(item["operation_id"]))
            if isinstance(op, AddTrigger) and item["event_path"] is None:
                trigger = find_trigger(model, op, exclude=dry.model.triggers.keys())
                if trigger is not None:
                    item["event_path"], item["event_name"] = trigger.path, trigger.name

    async def _fallback_import(self, runner: DaVinciRunner, failed: ToolResult) -> None:
        """The check refused the direct edit: undo the writer's files and import a delta."""
        deps, ctx, journal, dry = self.deps, self.ctx, self.journal, self.dry_run
        assert journal is not None and dry is not None and self.original_commit is not None
        commit = self.original_commit
        ctx.log("The workspace check failed: undoing the direct edit, importing a delta instead")
        records = journal.paths(step="write")
        tracked = {os.path.normcase(path) for path in deps.git.tracked_files(commit)}
        await ctx.run_blocking(
            lambda: restore_records(deps.git, commit, records, journal.backup_dir, tracked)
        )
        delta = journal.work_dir / "delta.arxml"
        change_set = apply_change_set(dry.outcome.change_set)
        problems = [
            f
            for f in await ctx.run_blocking(
                lambda: deps.writer.build_delta(dry.model, deps.config, change_set, delta)
            )
            if f.severity is Severity.ERROR
        ]
        if problems:
            raise _StepFailed(
                [
                    *problems,
                    _error(
                        codes.DV_FALLBACK_FAILED,
                        "The workspace check failed and the delta ARXML for the fallback "
                        "import could not be built.",
                    ),
                ]
            )
        result = await runner.import_arxml(delta, ctx)
        await self._tool_done("import", result)
        if not result.ok:
            raise self._tool_failure(
                codes.DV_FALLBACK_FAILED,
                "The workspace check failed and the fallback import failed too",
                result,
            )
        again = await runner.check_workspace(ctx)
        await self._tool_done("check", again)
        if not again.ok:
            raise self._tool_failure(
                codes.DV_FALLBACK_FAILED,
                "The workspace check failed again after the fallback import",
                again,
            )
        self.findings.append(
            Finding(
                codes.DV_FALLBACK_USED,
                Severity.WARNING,
                "The workspace check refused the direct ARXML edit "
                f"(exit code {failed.exit_code}). The change was imported as a delta ARXML "
                "instead and the check passed.",
            )
        )

    # ---- f. verify

    def _changed(self) -> tuple[list[str], list[str]]:
        """(changed paths the journal covers, changed paths it does not cover)."""
        journal = self.journal
        assert journal is not None
        known = {os.path.normcase(path) for path in self.untracked_before}
        report_rel = self._report_rel()
        covered: list[str] = []
        foreign: list[str] = []
        for entry in self.deps.git.status():
            if entry.untracked and os.path.normcase(entry.path) in known:
                continue
            if report_rel is not None and _inside(entry.path, (report_rel,)):
                continue
            (covered if journal.covers(entry.path) else foreign).append(entry.path)
        return covered, foreign

    def _foreign_problem(self, foreign: Sequence[str]) -> Finding:
        return _error(
            codes.VER_UNJOURNALED,
            f"{len(foreign)} file(s) changed during the apply without a journal entry: "
            f"{_listed(foreign)}. They are not touched by the rollback: check them by hand.",
        )

    def _verify(self, new_model: Model, apply_ops: Sequence[AnyOperation]) -> list[Finding]:
        dry = self.dry_run
        assert dry is not None
        problems = list(verify_model(dry.model, new_model, apply_ops))
        _covered, foreign = self._changed()
        if foreign:
            problems.append(self._foreign_problem(foreign))
        self._resolve_events(new_model, apply_ops)
        return problems

    # ---- g. packages, report, commit, close

    def _report_base(self) -> Path:
        base = Path(self.deps.settings.report_dir)
        return base if base.is_absolute() else self.deps.config.repo_root / base

    def _report_folder(self) -> Path:
        return self._report_base() / (self.branch or "apply")

    def _report_rel(self) -> str | None:
        return _relative(self.deps.config, self._report_base())

    def _failure_report_folder(self) -> Path:
        """Reports of failed applies must not dirty the repository."""
        name = (self.branch or "apply").replace("/", "-")
        stamp = self.deps.now().strftime("%H%M%S")
        if self._report_rel() is None:
            return self._report_base() / f"{name}-failed-{stamp}"
        return self.deps.journal_folder / "reports" / f"{name}-failed-{stamp}"

    def _journal_file(self, path: Path) -> None:
        journal = self.journal
        rel = _relative(self.deps.config, path)
        if journal is not None and rel is not None:
            journal.record(rel, "create")

    def _write_packages(self) -> None:
        dry = self.dry_run
        assert dry is not None
        change_set = dry.outcome.change_set
        if not change_set.routed(Route.MODEL) and not change_set.routed(Route.HOLD):
            return
        self.packages, findings = write_packages(
            self._report_folder(),
            model=dry.model,
            config=self.deps.config,
            outcome=dry.outcome,
            writer=self.deps.writer,
            before_write=self._journal_file,
        )
        self.findings.extend(findings)

    def _final_results(self) -> tuple[RequestResult, ...]:
        dry = self.dry_run
        assert dry is not None
        applied = set(self.write_outcome.applied_operation_ids) if self.write_outcome else set()
        routes = {op.id: op.route for op in dry.outcome.change_set.operations}
        final: list[RequestResult] = []
        for result in dry.outcome.results:
            mine = [i for i in result.operation_ids if routes.get(i) is Route.APPLY]
            if result.status is RequestStatus.ACCEPTED and mine and all(i in applied for i in mine):
                result = replace(result, status=RequestStatus.APPLIED)
            final.append(result)
        return tuple(final)

    def _report_data(self, *, ok: bool, rolled_back: bool, failure: str | None) -> ReportData:
        dry = self.dry_run
        return ReportData(
            ok=ok,
            rolled_back=rolled_back,
            repo_root=str(self.deps.config.repo_root),
            created=self.deps.now().strftime("%Y-%m-%d %H:%M:%S"),
            tool_version=porttool.__version__,
            original_branch=self.original_branch,
            original_commit=self.original_commit,
            branch=self.branch if ok else None,
            commit=self.commit_sha,
            results=self.final_results if ok else self._results,
            operations=dry.outcome.change_set.operations if dry is not None else (),
            applied_ids=frozenset(
                self.write_outcome.applied_operation_ids if self.write_outcome and ok else ()
            ),
            findings=tuple(self.findings),
            timings=tuple(self.timings),
            skipped_steps=self.skipped_steps,
            enable_hint=ENABLE_HINT,
            trigger_mappings=tuple(self.trigger_mappings),
            mapping_done=self.mapping_done,
            manual_instructions=self.manual_instructions,
            tool_logs=tuple(self.tool_logs),
            diff_stat=self.diff_stat,
            changed_files=self.changed_files,
            generated_files=self.generated_files,
            packages=self.packages,
            failure=failure,
        )

    def _committable(self, path: str) -> bool:
        config, settings = self.deps.config, self.deps.settings
        if settings.commit_generated or config.gen_data is None:
            return True
        rel = _relative(config, config.gen_data)
        return rel is None or not _inside(path, (rel,))

    def _stage_and_report(self) -> None:
        deps, git = self.deps, self.deps.git
        assert self.original_commit is not None
        covered, foreign = self._changed()
        if foreign:
            raise _StepFailed([self._foreign_problem(foreign)])
        to_add = [path for path in covered if self._committable(path)]
        if not to_add:
            raise _StepFailed(
                [_error(codes.COMMIT_EMPTY, "No file changed, so there is nothing to commit.")]
            )
        git.add_paths(to_add)
        self.changed_files = tuple(sorted(to_add))
        self.diff_stat = git.diff_stat_cached(self.original_commit)
        self.final_results = self._final_results()
        folder = self._report_folder()
        report = folder / REPORT_NAME
        self._journal_file(report)
        folder.mkdir(parents=True, exist_ok=True)
        rel = _relative(deps.config, folder)
        if rel is not None and not deps.settings.commit_reports and not git.is_ignored(rel):
            base = self._report_rel() or rel
            self.findings.append(
                Finding(
                    codes.REP_UNTRACKED,
                    Severity.INFO,
                    f"The report folder {base} is not ignored by git and is never committed. "
                    f"Add '{base}/' to .gitignore to keep 'git status' clean.",
                )
            )
        self.report_file = report
        atomic_write_text(
            report, render_report(self._report_data(ok=True, rolled_back=False, failure=None))
        )
        if rel is not None and deps.settings.commit_reports:
            git.add_paths([rel])

    def _commit_message(self) -> str:
        dry = self.dry_run
        assert dry is not None
        change_set = dry.outcome.change_set
        applied = set(self.write_outcome.applied_operation_ids) if self.write_outcome else set()
        groups = (
            ("Applied", [op for op in change_set.routed(Route.APPLY) if op.id in applied]),
            ("Waiting for model owner", list(change_set.routed(Route.MODEL))),
            ("On hold", list(change_set.routed(Route.HOLD))),
        )
        total = sum(len(operations) for _title, operations in groups)
        lines = [f"PortTool: {total} request(s) on {self.branch}", ""]
        for title, operations in groups:
            if not operations:
                continue
            lines.append(f"{title} ({len(operations)}):")
            for op in operations[:100]:
                who = ", ".join(part for part in (op.requester, source_text(op)) if part)
                lines.append(f"- {operation_text(op)}" + (f" [{who}]" if who else ""))
            if len(operations) > 100:
                lines.append(f"- ... and {len(operations) - 100} more (see the report)")
            lines.append("")
        base = f"{(self.original_commit or '')[:12]} ({self.original_branch or 'detached HEAD'})"
        lines.append(f"Base: {base}")
        return "\n".join(lines) + "\n"

    def _commit(self) -> None:
        git, journal = self.deps.git, self.journal
        assert journal is not None
        result = git.commit(self._commit_message())
        if not result.ok:
            output = result.output or "(git printed nothing)"
            if is_identity_problem(output):
                raise _StepFailed(
                    [_error(codes.COMMIT_IDENTITY, f"{IDENTITY_HELP}\n(git said: {output})")]
                )
            raise _StepFailed(
                [
                    _error(
                        codes.COMMIT_REJECTED,
                        "git refused the commit (a commit hook or another check failed). "
                        f"Output of git and its hooks:\n{output}",
                    )
                ]
            )
        self.commit_sha = git.head_commit()
        self.committed = True
        journal.note("commit", sha=self.commit_sha)
        self.ctx.log(f"Committed {(self.commit_sha or '')[:12]} on {self.branch}")

    def _close(self) -> None:
        journal = self.journal
        assert journal is not None
        if self.closed:
            return
        if self.report_file is not None and not self.deps.settings.commit_reports:
            # The same report again, now with the commit hash.
            try:
                atomic_write_text(
                    self.report_file,
                    render_report(self._report_data(ok=True, rolled_back=False, failure=None)),
                )
            except PortToolError as error:
                self.ctx.log(f"The report could not be updated with the commit: {error}")
        journal.finish("committed")
        self.closed = True

    # ------------------------------------------------------------------ failure and rollback

    def _cause(self, error: BaseException) -> tuple[list[Finding], str]:
        step = self.current_step
        if isinstance(error, _StepFailed):
            return list(error.findings), f"Step '{step}' failed: {error.findings[-1].message}"
        if isinstance(error, asyncio.CancelledError | JobCancelled):
            text = f"The apply was cancelled during step '{step}'."
            return [Finding(codes.APPLY_CANCELLED, Severity.WARNING, text)], text
        if isinstance(error, PortToolError):
            text = f"Step '{step}' failed: {error.user_message}"
        else:
            text = f"Step '{step}' failed unexpectedly: {type(error).__name__}: {error}"
        return [_error(codes.APPLY_FAILED, text)], text

    def _rollback(self, failure: str) -> tuple[bool, bool]:
        """Blocking. Returns (a rollback ran, it succeeded). Writes the failure report."""
        journal, git = self.journal, self.deps.git
        ran, good = journal is not None, True
        if journal is not None:
            self.ctx.log("Rolling back: restoring every journaled path")
            try:
                journal.release()
                state = read_journal(journal.file)
                if state is None:
                    raise PortToolError(f"The journal {journal.file} cannot be read back.")
                result: RestoreResult = rollback_from_journal(git, state, self.ctx.log)
                close_rolled_back(state, result)
                self.findings.extend(result.findings)
                self.findings.append(done_finding(result))
                good = not has_errors(result.findings)
            except (PortToolError, OSError) as error:
                good = False
                self.findings.append(
                    _error(
                        codes.RB_FAILED,
                        f"The rollback could not finish: {error}. The journal stays open: "
                        "close programs that use the repository, then run 'porttool "
                        "rollback'.",
                    )
                )
            self.ctx.log("Rollback finished" if good else "Rollback did NOT finish")
        try:
            folder = self._failure_report_folder()
            folder.mkdir(parents=True, exist_ok=True)
            data = self._report_data(ok=False, rolled_back=ran and good, failure=failure)
            atomic_write_text(folder / REPORT_NAME, render_report(data))
            self.report_file = folder / REPORT_NAME
        except (PortToolError, OSError) as error:
            self.report_file = None
            self.ctx.log(f"The failure report could not be written: {error}")
        return ran, good

    async def _on_failure(self, error: BaseException) -> ApplyReport:
        cancelled = isinstance(error, asyncio.CancelledError | JobCancelled)
        if self.committed:
            # The commit exists: the apply is complete. Finish the bookkeeping.
            try:
                await run_uncancellable(self.deps.rollback_pool, self._close)
            except (PortToolError, OSError) as close_error:
                self.ctx.log(f"The journal could not be archived: {close_error}")
            self.findings.append(
                Finding(
                    codes.APPLY_LATE_CANCEL,
                    Severity.WARNING,
                    f"The commit {(self.commit_sha or '')[:12]} was already made when "
                    + ("the cancel request arrived" if cancelled else f"this happened: {error}")
                    + ". The apply is complete and was not rolled back.",
                )
            )
            self._success_report()
            assert self.report is not None
            return self.report
        cause, failure = self._cause(error)
        self.findings.extend(cause)
        self.ctx.log(failure)
        ran, good = await run_uncancellable(
            self.deps.rollback_pool, lambda: self._rollback(failure)
        )
        report_file = str(self.report_file) if self.report_file is not None else None
        self.report = ApplyReport(
            ok=False,
            branch=None,
            results=self._results,
            findings=tuple(self.findings),
            report_file=report_file,
            rolled_back=ran and good,
        )
        if cancelled:
            raise error
        if isinstance(error, _StepFailed) and (good or not ran):
            return self.report
        state = (
            "The repository was rolled back to its state before the apply."
            if good
            else "The rollback did NOT finish: run 'porttool rollback'."
        )
        raise ApplyFailedError(
            f"{failure} {state}" + (f" Report: {report_file}" if report_file else ""),
            detail=repr(error),
        ) from error


# ---------------------------------------------------------------------- recovery


async def recover(
    ctx: LoggingContext,
    config: ProjectConfig,
    *,
    git: Git,
    journal_folder: Path,
    lock_dir: Path | None,
    rollback_pool: Executor,
) -> ApplyReport:
    """Roll back the apply whose journal is open, from the journal alone.

    The process that wrote the journal is gone (or failed to roll back). The restoration
    is the same code a failed apply runs. It cannot be cancelled once it has started.
    """
    lock = RepoLock(config.repo_root, lock_dir=lock_dir)

    def work() -> ApplyReport:
        try:
            lock.acquire()
        except RepoLockedError as error:
            return ApplyReport(False, None, (), (_error(codes.PRE_LOCK, error.user_message),))
        try:
            file = open_journal_file(journal_folder)
            state = read_journal(file) if file is not None else None
            if state is None:
                note = Finding(
                    codes.RB_NOTHING,
                    Severity.INFO,
                    "There is no unfinished apply to roll back. To discard a finished apply, "
                    "switch back to your branch and delete the porttool/... branch with git.",
                )
                ctx.log(note.message)
                return ApplyReport(True, None, (), (note,))
            ctx.progress(0, 2, f"Rolling back the unfinished apply on {state.branch or '?'}")
            try:
                result = rollback_from_journal(git, state, ctx.log)
                close_rolled_back(state, result)
            except (PortToolError, OSError) as error:
                problem = _error(
                    codes.RB_FAILED,
                    f"The rollback could not finish: {error}. The journal stays open; fix "
                    "the cause and run the rollback again.",
                )
                ctx.log(problem.message)
                return ApplyReport(False, None, (), (problem,))
            findings = (*result.findings, done_finding(result))
            ctx.progress(2, 2, findings[-1].message)
            return ApplyReport(
                ok=not has_errors(findings),
                branch=result.branch_kept,
                results=(),
                findings=findings,
                rolled_back=True,
            )
        finally:
            lock.release()

    return await run_uncancellable(rollback_pool, work)
