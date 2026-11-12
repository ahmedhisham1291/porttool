"""Command line of the writer: dry run and delta file for a change set in JSON.

    porttool writer-plan  [--repo DIR] CHANGESET.json
    porttool writer-delta [--repo DIR] CHANGESET.json OUT.arxml
    porttool writer-codes

Applying a change set is not a writer command: it needs the git branch, the journal and
the checks of the pipeline. Exit codes: 0 success, 1 findings of severity error, 2 a
``PortToolError`` (printed as ``[code] message``, no traceback).
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import click

from porttool.core.config import ProjectConfig
from porttool.core.config_loader import load_config
from porttool.core.errors import ConfigError, PortToolError
from porttool.core.models import Model
from porttool.core.operations import ChangeSet
from porttool.core.results import Finding, Severity
from porttool.writer import codes
from porttool.writer.writer import ArxmlWriter

_FILE = click.Path(exists=True, dir_okay=False, path_type=Path)
_FOLDER = click.Path(exists=True, file_okay=False, path_type=Path)


def _config(context: click.Context, repo: Path | None) -> ProjectConfig:
    if repo is not None:
        return load_config(repo).config
    inherited = context.find_object(ProjectConfig)
    if inherited is None:
        raise ConfigError("No project is configured. Pass --repo with the ECU repository folder.")
    return inherited


def _load(config: ProjectConfig) -> Model:
    # The reader's public entry point for command lines; imported here so that the
    # writer package itself depends on porttool.core only.
    from porttool.reader import load_blocking

    return load_blocking(config)[0]


def _change_set(file: Path) -> ChangeSet:
    try:
        return ChangeSet.from_json(file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ConfigError(f"{file} is not a change set: {error}") from error


def _finish(findings: Iterable[Finding]) -> None:
    failed = False
    for finding in findings:
        where = f" ({finding.file})" if finding.file else ""
        click.echo(f"{finding.severity.value.upper()} {finding.code}: {finding.message}{where}")
        failed = failed or finding.severity is Severity.ERROR
    if failed:
        raise click.exceptions.Exit(1)


def _fail(error: PortToolError) -> None:
    click.echo(str(error), err=True)
    raise click.exceptions.Exit(2)


@click.command("writer-plan", help="Dry run: show what a change set (JSON) would write.")
@click.option("--repo", type=_FOLDER, help="Root folder of the ECU repository.")
@click.argument("change_set", type=_FILE)
@click.pass_context
def plan_command(context: click.Context, repo: Path | None, change_set: Path) -> None:
    try:
        config = _config(context, repo)
        plan = ArxmlWriter().plan(
            config=config, model=_load(config), change_set=_change_set(change_set)
        )
    except PortToolError as error:
        _fail(error)
        return
    for line in plan.preview:
        click.echo(line)
    for file in plan.files:
        click.echo(f"{file.action} {file.rel_path}: {file.summary}")
    _finish(plan.findings)


@click.command("writer-delta", help="Write a delta ARXML with every element a change set touches.")
@click.option("--repo", type=_FOLDER, help="Root folder of the ECU repository.")
@click.argument("change_set", type=_FILE)
@click.argument("target", type=click.Path(dir_okay=False, path_type=Path))
@click.pass_context
def delta_command(
    context: click.Context, repo: Path | None, change_set: Path, target: Path
) -> None:
    try:
        config = _config(context, repo)
        findings = ArxmlWriter().build_delta(
            _load(config), config, _change_set(change_set), target.absolute()
        )
    except PortToolError as error:
        _fail(error)
        return
    _finish(findings)


@click.command("writer-codes", help="List the finding codes of the writer.")
def codes_command() -> None:
    for name in sorted(vars(codes)):
        value = getattr(codes, name)
        if name.isupper() and isinstance(value, str):
            click.echo(value)


def register_cli(group: click.Group) -> None:
    group.add_command(plan_command)
    group.add_command(delta_command)
    group.add_command(codes_command)
