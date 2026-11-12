"""Commands of porttool.davinci: ``porttool davinci doctor | check | validate | generate |
show-commands``.

Thin on purpose: load porttool.toml of ``--repo``, call the tested functions through the
engine, print the result. Exit code 0 on success, 1 when a tool failed or a finding has
severity error, 2 on a PortToolError (license, timeout, bad configuration).
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import click

from porttool.cli import echo_findings
from porttool.core.config_loader import load_config
from porttool.core.interfaces import JobContext, ToolResult
from porttool.core.results import Severity
from porttool.davinci.runner import DaVinciToolRunner
from porttool.davinci.settings import TOOL_OF_ACTION
from porttool.davinci.templates import toml_list
from porttool.engine import run_sync

_REPO = click.option(
    "--repo",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("."),
    show_default=True,
    help="Root folder of the ECU repository (the folder of porttool.toml).",
)


def _echo(text: str) -> None:
    """Print text even when the console cannot show every character."""
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        shown = text.encode(encoding, errors="backslashreplace").decode(encoding)
    except LookupError:
        shown = text.encode("ascii", errors="backslashreplace").decode("ascii")
    click.echo(shown)


def _load(repo: Path) -> DaVinciToolRunner:
    """The runner for ``repo``. A broken porttool.toml raises ConfigError (exit code 2)."""
    loaded = load_config(repo, strict=False)
    for finding in loaded.findings:
        if finding.severity is not Severity.INFO:
            _echo(f"{finding.severity.value.upper()} {finding.code}: {finding.message}")
    return DaVinciToolRunner(loaded.config)


def _live(done: int, total: int, message: str) -> None:
    if message:
        _echo(f"  {message}")


def _run_tool(
    runner: DaVinciToolRunner,
    name: str,
    job: Callable[[JobContext], Coroutine[Any, Any, ToolResult]],
) -> None:
    result = run_sync(job, on_progress=_live, name=name)
    code = echo_findings(result.findings)
    verdict = "ok" if result.ok else "FAILED"
    exit_text = "not started" if result.exit_code is None else f"exit code {result.exit_code}"
    _echo(f"{result.tool}: {verdict} ({exit_text}, {result.elapsed_s:.1f} s)")
    if result.log_file is not None:
        _echo(f"Complete output: {result.log_file}")
    raise click.exceptions.Exit(code if result.ok else 1)


@click.group("davinci")
def davinci_group() -> None:
    """Check and run the Vector tools (DVWspChecker, DVImEx, DVSwcGen, DVCfgCmd)."""


@davinci_group.command("doctor")
@_REPO
def doctor(repo: Path) -> None:
    """Say what must be fixed before a DaVinci step can run. Starts no tool."""
    runner = _load(repo)
    findings = run_sync(runner.doctor, name="davinci doctor")
    raise click.exceptions.Exit(echo_findings(findings))


@davinci_group.command("show-commands")
@_REPO
def show_commands(repo: Path) -> None:
    """Print the command line of every action and whether it is UNVERIFIED. Runs nothing."""
    runner = _load(repo)
    for prepared in runner.rendered_commands():
        template = runner.settings.commands[prepared.action]
        _echo(f"{prepared.action} ({TOOL_OF_ACTION[prepared.action]}): {template.label}")
        if template.usable:
            _echo(f"  template: {toml_list(template.args)}")
        if prepared.rendered is not None:
            _echo(f"  command:  {prepared.rendered.display()}")
        else:
            _echo("  command:  cannot be built yet:")
        if not prepared.runnable and prepared.rendered is not None:
            _echo("  it would not be started as it is:")
        for finding in prepared.findings:
            _echo(f"    {finding.severity.value.upper()} {finding.code}: {finding.message}")
    _echo("Nothing was started. <...> stands for a value that is known only at run time.")


@davinci_group.command("check")
@_REPO
def check(repo: Path) -> None:
    """Check the DaVinci Developer workspace with DVWspChecker."""
    runner = _load(repo)
    _run_tool(runner, "davinci check", runner.check_workspace)


@davinci_group.command("validate")
@_REPO
def validate(repo: Path) -> None:
    """Validate the DaVinci Configurator project with DVCfgCmd."""
    runner = _load(repo)
    _run_tool(runner, "davinci validate", runner.validate_project)


@davinci_group.command("generate")
@_REPO
@click.option(
    "--modules",
    default="",
    help="Modules to generate, separated by commas, e.g. Rte,Os. Default: [generate] of "
    "porttool.toml.",
)
def generate(repo: Path, modules: str) -> None:
    """Generate code (by default RTE and OS) with DVCfgCmd."""
    runner = _load(repo)
    names = [name.strip() for name in modules.split(",") if name.strip()]

    async def job(ctx: JobContext) -> ToolResult:
        return await runner.generate(names, ctx)

    _run_tool(runner, "davinci generate", job)


def register_cli(group: click.Group) -> None:
    """Add the ``davinci`` command group to the root command line."""
    group.add_command(davinci_group)
