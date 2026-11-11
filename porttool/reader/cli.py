"""Command line of the reader: ``load`` and ``list swc|ports|connectors|unconnected``.

Thin click wiring: parse the arguments, load, print what ``report.py`` returns.

Exit codes: 0 success, 1 the model has findings of severity error, 2 a ``PortToolError``
(printed as ``[code] message`` without traceback).

Where the configuration comes from: if a parent command stored a ``ProjectConfig`` in the
click context (``ctx.obj``), it is used. Otherwise the repository is described with
``--repo`` and the other options below.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import click

from porttool.core.config import ProjectConfig
from porttool.core.errors import ConfigError, PortToolError
from porttool.core.models import Model
from porttool.reader import report
from porttool.reader.reader import LoadStats, ReaderOptions
from porttool.reader.standalone import load_blocking

_F = TypeVar("_F", bound=Callable[..., Any])

EXIT_OK = 0
EXIT_ERROR_FINDINGS = 1
EXIT_PORTTOOL_ERROR = 2

_PATH = click.Path(path_type=Path)


def _config_options(command: _F) -> _F:
    """Options that describe the repository when no ProjectConfig is in the context."""
    decorators = (
        click.option("--repo", type=_PATH, help="Root folder of the ECU repository."),
        click.option(
            "--arxml-root",
            "arxml_roots",
            type=_PATH,
            multiple=True,
            help="Folder or file with ARXML to load. Repeatable. Default: the repository root.",
        ),
        click.option("--ecuc", type=_PATH, help="ECUC file (task mapping and OS tasks)."),
        click.option("--dcf", type=_PATH, help="DaVinci Developer workspace file (.dcf)."),
        click.option(
            "--exclude",
            "excludes",
            multiple=True,
            help="Pattern of files or folders never loaded, relative to the repository. "
            "Repeatable.",
        ),
        click.option("--cache-dir", type=_PATH, help="Folder of the record cache."),
        click.option("--no-cache", is_flag=True, help="Parse every file, ignore the cache."),
    )
    for decorator in reversed(decorators):
        command = decorator(command)
    return command


def _absolute(path: Path, base: Path) -> Path:
    return path if path.is_absolute() else (base / path).resolve()


def resolve_config(
    context: click.Context,
    repo: Path | None,
    arxml_roots: tuple[Path, ...],
    ecuc: Path | None,
    dcf: Path | None,
    excludes: tuple[str, ...],
    cache_dir: Path | None,
) -> ProjectConfig:
    """The ProjectConfig of this invocation: from the click context or from the options."""
    inherited = context.find_object(ProjectConfig)
    if repo is None:
        if inherited is None:
            raise ConfigError(
                "No project is configured. Pass --repo (and --arxml-root), "
                "or start PortTool with a project configuration."
            )
        if arxml_roots or ecuc or dcf or excludes or cache_dir:
            raise ConfigError(
                "--arxml-root, --ecuc, --dcf, --exclude and --cache-dir need --repo; "
                "without --repo the project configuration is used as it is."
            )
        return inherited
    root = repo.resolve()
    if not root.is_dir():
        raise ConfigError(f"The repository folder {root} does not exist.")
    return ProjectConfig(
        repo_root=root,
        arxml_roots=tuple(_absolute(p, root) for p in arxml_roots) or (root,),
        exclude_globs=excludes,
        ecuc_file=None if ecuc is None else _absolute(ecuc, root),
        dcf_file=None if dcf is None else _absolute(dcf, root),
        cache_dir=None if cache_dir is None else cache_dir.resolve(),
    )


def _guarded(command: _F) -> _F:
    """Print a PortToolError as ``[code] message`` and exit with 2."""

    @functools.wraps(command)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return command(*args, **kwargs)
        except PortToolError as error:
            click.echo(str(error), err=True)
            raise click.exceptions.Exit(EXIT_PORTTOOL_ERROR) from None

    return wrapper  # type: ignore[return-value]


def _load(context: click.Context, options: dict[str, Any]) -> tuple[Model, LoadStats]:
    config = resolve_config(
        context,
        options["repo"],
        options["arxml_roots"],
        options["ecuc"],
        options["dcf"],
        options["excludes"],
        options["cache_dir"],
    )
    return load_blocking(config, options=ReaderOptions(use_cache=not options["no_cache"]))


def _exit_code(model: Model) -> int:
    return EXIT_ERROR_FINDINGS if report.has_errors(model) else EXIT_OK


def _print_rows(
    headers: report.Row, rows: list[report.Row], output_format: str, model: Model
) -> None:
    formatter = report.format_tsv if output_format == "tsv" else report.format_table
    click.echo(formatter(headers, rows))
    raise click.exceptions.Exit(_exit_code(model))


_format_option = click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "tsv"]),
    default="table",
    show_default=True,
    help="Output format.",
)


@click.command("load")
@_config_options
@click.option(
    "--max-findings",
    type=click.IntRange(min=0),
    default=50,
    show_default=True,
    help="How many findings to print (0: none).",
)
@click.pass_context
@_guarded
def load_command(context: click.Context, /, max_findings: int, **options: Any) -> None:
    """Load the repository and print what was found."""
    model, stats = _load(context, options)
    for line in report.summary_lines(model, stats):
        click.echo(line)
    if max_findings:
        for line in report.finding_lines(model.findings, max_findings):
            click.echo(line)
    raise click.exceptions.Exit(_exit_code(model))


@click.group("list")
def list_group() -> None:
    """List what the repository contains."""


@list_group.command("swc")
@_config_options
@_format_option
@click.pass_context
@_guarded
def list_swc(context: click.Context, /, output_format: str, **options: Any) -> None:
    """Software component types."""
    model, _stats = _load(context, options)
    _print_rows(report.SWC_HEADERS, report.swc_rows(model), output_format, model)


@list_group.command("ports")
@_config_options
@_format_option
@click.option("--swc", help="Only the ports of this component (short name or path).")
@click.pass_context
@_guarded
def list_ports(
    context: click.Context, /, output_format: str, swc: str | None, **options: Any
) -> None:
    """Ports of the software component types."""
    model, _stats = _load(context, options)
    _print_rows(report.PORT_HEADERS, report.port_rows(model, swc), output_format, model)


@list_group.command("connectors")
@_config_options
@_format_option
@click.pass_context
@_guarded
def list_connectors(context: click.Context, /, output_format: str, **options: Any) -> None:
    """Assembly and delegation connectors."""
    model, _stats = _load(context, options)
    _print_rows(report.CONNECTOR_HEADERS, report.connector_rows(model), output_format, model)


@list_group.command("unconnected")
@_config_options
@_format_option
@click.pass_context
@_guarded
def list_unconnected(context: click.Context, /, output_format: str, **options: Any) -> None:
    """Port instances without a connector."""
    model, _stats = _load(context, options)
    _print_rows(report.UNCONNECTED_HEADERS, report.unconnected_rows(model), output_format, model)


def register_cli(group: click.Group) -> None:
    """Add the reader's commands (``load``, ``list``) to the PortTool command group."""
    group.add_command(load_command)
    group.add_command(list_group)
