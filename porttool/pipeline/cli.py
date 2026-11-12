"""Pipeline commands: ``init``, ``export``, ``dry-run``, ``apply``, ``rollback``, ``status``.

Thin: each command opens the repository through the same ``PortToolService`` the GUI
uses, starts a job, prints its progress lines, a result table and the report path.

Exit codes: 0 success; 1 the command finished and reported a finding of severity error
(a rejected request, a refused or rolled-back apply); 2 a ``PortToolError`` or a cancel.
"""

from __future__ import annotations

import functools
import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any, TypeVar

import click

from porttool.core.config_loader import CONFIG_FILE_NAME, discover_paths, save_config
from porttool.core.errors import PortToolError
from porttool.core.interfaces import JobView
from porttool.core.results import Finding, RequestResult, Severity
from porttool.pipeline import service as service_module
from porttool.pipeline.describe import operation_text
from porttool.pipeline.journal import ARCHIVE_DIR, open_journal_file
from porttool.pipeline.pendingio import read_pending
from porttool.pipeline.service import PortToolService
from porttool.pipeline.settings import DAVINCI_STEPS

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_ERROR = 2
_POLL_S = 0.05
_FINAL = frozenset({"done", "failed", "cancelled"})
_F = TypeVar("_F", bound=Callable[..., Any])

_REPO = click.option(
    "--repo",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("."),
    show_default="current folder",
    help="Root folder of the ECU repository (holds porttool.toml).",
)


def _guarded(function: _F) -> _F:
    """Turn a ``PortToolError`` into ``[code] message`` on stderr and exit code 2."""

    @functools.wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return function(*args, **kwargs)
        except PortToolError as error:
            click.echo(str(error), err=True)
            raise click.exceptions.Exit(EXIT_ERROR) from error

    return wrapper  # type: ignore[return-value]


def _echo_findings(findings: Iterable[Finding]) -> bool:
    """Print one line per finding. True when one of them is an error."""
    bad = False
    for finding in findings:
        where = f" ({finding.file})" if finding.file else ""
        click.echo(f"{finding.severity.value.upper()} {finding.code}: {finding.message}{where}")
        bad = bad or finding.severity is Severity.ERROR
    return bad


def _open(repo: Path) -> PortToolService:
    """Build the service and open the repository. The caller closes the service."""
    service = service_module.build_service()
    try:
        _echo_findings(f for f in service.open_repo(repo) if f.severity is not Severity.INFO)
    except BaseException:
        service.close()
        raise
    return service


def _follow(service: PortToolService, job_id: str) -> JobView:
    """Print the progress lines of a job until it has finished. Ctrl+C cancels the job."""
    index = 0
    cancelling = False
    while True:
        try:
            view = service.job(job_id)
            lines, index = service.job_log(job_id, index)
            for line in lines:
                click.echo(f"  {line}")
            if view is None or view.state in _FINAL:
                break
            time.sleep(_POLL_S)
        except KeyboardInterrupt:
            if cancelling:
                raise
            cancelling = True
            click.echo("Cancelling. A running apply is rolled back first; please wait.")
            service.cancel(job_id)
    if view is None:
        raise PortToolError(f"The job {job_id} disappeared.")
    lines, index = service.job_log(job_id, index)
    for line in lines:
        click.echo(f"  {line}")
    if view.state == "failed":
        click.echo(view.error or "The job failed.", err=True)
        raise click.exceptions.Exit(EXIT_ERROR)
    if view.state == "cancelled":
        click.echo("Cancelled.", err=True)
        raise click.exceptions.Exit(EXIT_ERROR)
    return view


def _result_table(service: PortToolService, results: Sequence[RequestResult]) -> bool:
    """Print the request table. True when a request carries an error finding."""
    outcome = service.last_validation()
    operations = {op.id: op for op in outcome.change_set.operations} if outcome else {}
    rows: list[tuple[str, str, str]] = []
    bad = False
    for result in results:
        if result.row is not None:
            name = result.row.file.replace("\\", "/").rsplit("/", 1)[-1]
            where = f"{name} {result.row.sheet} row {result.row.row}"
        else:
            where = result.request_id
        errors = [f for f in result.findings if f.severity is Severity.ERROR]
        bad = bad or bool(errors)
        if errors:
            detail = "; ".join(f"{f.code}: {f.message}" for f in errors)
        else:
            detail = "; ".join(
                operation_text(operations[i]) for i in result.operation_ids if i in operations
            )
        rows.append((result.status.value, where, detail))
    if not rows:
        click.echo("No requests.")
        return False
    widths = [max(len(row[i]) for row in [*rows, ("STATUS", "REQUEST", "")]) for i in range(2)]
    click.echo(f"{'STATUS':<{widths[0]}}  {'REQUEST':<{widths[1]}}  DETAIL")
    for status, where, detail in rows:
        click.echo(f"{status:<{widths[0]}}  {where:<{widths[1]}}  {detail}")
    counts: dict[str, int] = {}
    for status, _where, _detail in rows:
        counts[status] = counts.get(status, 0) + 1
    click.echo("Total: " + ", ".join(f"{n} {status}" for status, n in sorted(counts.items())))
    return bad


def _dry_run(service: PortToolService, workbooks: Sequence[Path]) -> tuple[JobView, bool]:
    """Load, import, validate, plan; print the result. Returns (job view, errors found)."""
    click.echo(f"Dry run of {len(workbooks)} workbook(s):")
    view = _follow(service, service.start_dry_run(workbooks))
    outcome, plan = service.last_validation(), service.last_plan()
    if outcome is None or plan is None:
        raise PortToolError("The dry run produced no result.")
    bad = _result_table(service, outcome.results)
    bad = _echo_findings(outcome.findings) or bad
    bad = _echo_findings(plan.findings) or bad
    if plan.files:
        click.echo(f"Plan: {len(plan.files)} file(s) would change:")
        for file in plan.files:
            click.echo(f"  {file.action:<7} {file.rel_path}  ({file.summary})")
    else:
        click.echo("Plan: no workspace file would change.")
    return view, bad


@click.command("init")
@_REPO
@click.option(
    "--dpa",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="The DaVinci project file, when the repository holds several .dpa files.",
)
@click.option("--force", is_flag=True, help=f"Overwrite an existing {CONFIG_FILE_NAME}.")
@_guarded
def init_command(repo: Path, dpa: Path | None, force: bool) -> None:
    """Find the project files in the repository and write porttool.toml."""
    found = discover_paths(repo, dpa)
    for title, paths in (
        ("Project file (.dpa)", found.dpa_files),
        ("Workspace file (.dcf)", found.dcf_files),
        ("ECUC file", found.ecuc_files),
        ("Flat extract", found.flat_extracts),
        ("Flat map", found.flat_maps),
        ("ARXML folders", found.arxml_roots),
        ("Generated folder", found.gen_data_dirs),
    ):
        click.echo(f"{title}:")
        for index, path in enumerate(paths):
            chosen = "*" if index == 0 or title == "ARXML folders" else " "
            click.echo(f"  {chosen} {path}")
        if not paths:
            click.echo("    (none found)")
    bad = _echo_findings(found.findings)
    target = found.repo_root / CONFIG_FILE_NAME
    if target.exists() and not force:
        click.echo(f"{target} exists already and was not changed. Use --force to overwrite it.")
        raise click.exceptions.Exit(EXIT_FINDINGS)
    written = save_config(found.propose(), target)
    click.echo(f"Written: {written} (the entries marked * were chosen; check them)")
    click.echo(
        "DaVinci steps are off: 'porttool apply' edits the ARXML on a new git branch, "
        "verifies by reloading and commits. Enable steps later in [pipeline] davinci_steps."
    )
    raise click.exceptions.Exit(EXIT_FINDINGS if bad else EXIT_OK)


@click.command("export")
@_REPO
@click.option(
    "--out",
    "target",
    required=True,
    type=click.Path(dir_okay=False, path_type=Path),
    help="The workbook to write (.xlsx).",
)
@_guarded
def export_command(repo: Path, target: Path) -> None:
    """Load the repository and write the Excel workbook for change requests."""
    service = _open(repo)
    try:
        view = _follow(service, service.start_export(target))
        summary = view.summary or {}
        messages = summary.get("messages")
        for message in messages if isinstance(messages, list) else []:
            click.echo(str(message))
        if not summary.get("ok"):
            click.echo("The workbook was not written.", err=True)
            raise click.exceptions.Exit(EXIT_FINDINGS)
        click.echo(f"Workbook: {summary.get('target')}")
    finally:
        service.close()


@click.command("dry-run")
@_REPO
@click.argument(
    "workbooks", nargs=-1, required=True, type=click.Path(dir_okay=False, path_type=Path)
)
@_guarded
def dry_run_command(repo: Path, workbooks: tuple[Path, ...]) -> None:
    """Import WORKBOOKS, validate every request and show what an apply would do."""
    service = _open(repo)
    try:
        _view, bad = _dry_run(service, workbooks)
        click.echo("Nothing was changed.")
        raise click.exceptions.Exit(EXIT_FINDINGS if bad else EXIT_OK)
    finally:
        service.close()


@click.command("apply")
@_REPO
@click.option("--yes", is_flag=True, help="Do not ask for confirmation.")
@click.argument(
    "workbooks", nargs=-1, required=True, type=click.Path(dir_okay=False, path_type=Path)
)
@_guarded
def apply_command(repo: Path, yes: bool, workbooks: tuple[Path, ...]) -> None:
    """Dry run of WORKBOOKS, then apply the accepted requests on a new git branch."""
    service = _open(repo)
    try:
        view, bad = _dry_run(service, workbooks)
        summary = view.summary or {}
        if summary.get("nothing_to_do"):
            click.echo("Nothing to apply.")
            raise click.exceptions.Exit(EXIT_FINDINGS if bad else EXIT_OK)
        if not summary.get("can_apply"):
            click.echo("The dry run reported errors that block the apply.", err=True)
            raise click.exceptions.Exit(EXIT_FINDINGS)
        steps = ", ".join(service.settings().davinci_steps) or "none"
        click.echo(f"DaVinci steps that will run: {steps}")
        if not yes and not click.confirm("Apply these requests on a new git branch?"):
            click.echo("Nothing was changed.")
            raise click.exceptions.Exit(EXIT_OK)
        click.echo("Apply:")
        _follow(service, service.start_apply())
        report = service.last_report()
        if report is None:
            raise PortToolError("The apply produced no report.")
        bad = _result_table(service, report.results) or bad
        bad = _echo_findings(report.findings) or bad
        if report.ok:
            click.echo(f"Applied on branch: {report.branch}")
        elif report.rolled_back:
            click.echo("The apply failed. The repository was rolled back.", err=True)
        else:
            click.echo("The apply was refused. Nothing was changed.", err=True)
        if report.report_file:
            click.echo(f"Report: {report.report_file}")
        raise click.exceptions.Exit(EXIT_OK if report.ok and not bad else EXIT_FINDINGS)
    finally:
        service.close()


@click.command("rollback")
@_REPO
@_guarded
def rollback_command(repo: Path) -> None:
    """Undo an apply that did not finish (after a crash, a kill or a power loss)."""
    service = _open(repo)
    try:
        _follow(service, service.start_rollback())
        report = service.last_report()
        if report is None:
            raise PortToolError("The rollback produced no report.")
        bad = _echo_findings(report.findings)
        raise click.exceptions.Exit(EXIT_OK if report.ok and not bad else EXIT_FINDINGS)
    finally:
        service.close()


@click.command("status")
@_REPO
@_guarded
def status_command(repo: Path) -> None:
    """Show the state of the repository as PortTool sees it."""
    service = _open(repo)
    try:
        config = service.config()
        assert config is not None
        settings = service.settings()
        click.echo(f"Repository: {config.repo_root}")
        git = service.git_for(config)
        if git.executable() is None:
            click.echo("git: not found on PATH")
        elif not git.inside_work_tree():
            click.echo("git: not a git work tree")
        else:
            entries = git.status()
            changed = sum(1 for entry in entries if not entry.untracked)
            untracked = len(entries) - changed
            click.echo(f"Branch: {git.current_branch() or 'detached HEAD'}")
            click.echo(f"Commit: {git.head_commit() or 'none'}")
            click.echo(f"Working tree: {changed} changed, {untracked} untracked file(s)")
        pending = read_pending(config)
        click.echo(
            f"Pending queue: {len(pending.queue.entries)} entr(y/ies) in {config.pending_file}"
        )
        bad = _echo_findings(pending.findings)
        enabled = ", ".join(settings.davinci_steps) or "none"
        skipped = ", ".join(s for s in DAVINCI_STEPS if not settings.enabled(s)) or "none"
        click.echo(f"DaVinci steps enabled: {enabled}; not enabled: {skipped}")
        folder = service.journal_folder(config, settings)
        click.echo(f"Journals: {folder}")
        archived = sorted((folder / ARCHIVE_DIR).glob("*.jsonl"))
        if archived:
            click.echo(f"Last finished apply: {archived[-1].stem}")
        if open_journal_file(folder) is not None:
            click.echo(
                "RECOVERY NEEDED: an apply did not finish. Run 'porttool rollback'.", err=True
            )
            bad = True
        else:
            click.echo("No unfinished apply.")
        raise click.exceptions.Exit(EXIT_FINDINGS if bad else EXIT_OK)
    finally:
        service.close()


def register_cli(group: click.Group) -> None:
    """Add the pipeline commands to the root command group."""
    for command in (
        init_command,
        export_command,
        dry_run_command,
        apply_command,
        rollback_command,
        status_command,
    ):
        group.add_command(command)
