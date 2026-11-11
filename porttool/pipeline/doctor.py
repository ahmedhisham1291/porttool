"""``doctor``: checks of the environment that need no change to the repository."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from porttool.core.config import ProjectConfig
from porttool.core.errors import GitError, PortToolError
from porttool.core.interfaces import DaVinciRunner, JobContext
from porttool.core.results import Finding, Severity
from porttool.pipeline import codes
from porttool.pipeline.gitrepo import IDENTITY_HELP, Git, GitMissingError
from porttool.pipeline.journal import open_journal_file
from porttool.pipeline.settings import DAVINCI_STEPS, PipelineSettings


def git_doctor(git: Git) -> tuple[Finding, ...]:
    """git executable, work tree and identity. Blocking."""

    def note(
        message: str, severity: Severity = Severity.ERROR, code: str = codes.DOC_GIT
    ) -> Finding:
        return Finding(code=code, severity=severity, message=message)

    try:
        found = [
            note(f"git found: {git.version()} ({git.executable()})", Severity.INFO, codes.DOC_OK)
        ]
        if not git.inside_work_tree():
            return (*found, note(f"{git.repo_root} is not inside a git work tree."))
        if git.head_commit() is None:
            found.append(note("The git repository has no commit yet."))
        problem = git.identity_problem()
        if problem is not None:
            found.append(note(IDENTITY_HELP))
        return tuple(found)
    except GitMissingError as error:
        return (note(error.user_message),)
    except GitError as error:
        return (note(error.user_message),)


async def run_doctor(
    ctx: JobContext,
    config: ProjectConfig,
    settings: PipelineSettings,
    settings_findings: Sequence[Finding],
    *,
    git: Git,
    runner_factory: Callable[[ProjectConfig], DaVinciRunner],
    journal_folder: Path,
) -> tuple[Finding, ...]:
    findings: list[Finding] = list(settings_findings)
    ctx.progress(0, 3, "Checking git")
    findings.extend(await ctx.run_blocking(lambda: git_doctor(git)))
    if await ctx.run_blocking(lambda: open_journal_file(journal_folder)) is not None:
        findings.append(
            Finding(
                code=codes.REC_NEEDED,
                severity=Severity.ERROR,
                message="An earlier apply did not finish. Run 'porttool rollback'.",
            )
        )
    enabled = ", ".join(settings.davinci_steps) or "none"
    skipped = ", ".join(step for step in DAVINCI_STEPS if not settings.enabled(step)) or "none"
    findings.append(
        Finding(
            code=codes.DOC_OK,
            severity=Severity.INFO,
            message=f"DaVinci steps enabled in [pipeline] davinci_steps: {enabled}; "
            f"not enabled: {skipped}.",
        )
    )
    ctx.progress(1, 3, "Checking the DaVinci tools")
    try:
        runner = runner_factory(config)
    except PortToolError as error:
        findings.append(
            Finding(
                code=codes.DOC_RUNNER,
                severity=Severity.ERROR if settings.any_davinci_step else Severity.WARNING,
                message=f"The DaVinci runner is not available: {error.user_message}",
            )
        )
    else:
        findings.extend(await runner.doctor(ctx))
    ctx.progress(3, 3, "Doctor finished")
    return tuple(findings)
