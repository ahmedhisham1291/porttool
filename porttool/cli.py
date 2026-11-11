"""Root command line: ``porttool [--log-dir DIR] [-v] <command> ...``.

``main`` is the root ``click.Group``. On first use it calls ``register_cli(group)`` of
every porttool subpackage that has a ``cli`` module, so each package brings its own
commands. This module only wires things together: it sets up logging, loads the
commands and turns errors into exit codes.

Exit codes:

* 0  success
* 1  the command finished and reported findings of severity error
* 2  a ``PortToolError`` (printed as ``[code] message`` plus the log file, no traceback);
     click also uses 2 for a wrong command line
* 3  an unexpected error (the traceback is in the log file)
"""

from __future__ import annotations

import importlib
import logging
import os
import pkgutil
from collections.abc import Iterable
from pathlib import Path
from types import ModuleType
from typing import Any

import click

import porttool
from porttool.core.errors import PortToolError
from porttool.core.log import current_log_file, install_exception_hooks, setup_logging
from porttool.core.results import Finding, Severity

_logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_ERROR = 2
EXIT_UNEXPECTED = 3


def exit_code_for(findings: Iterable[Finding]) -> int:
    """1 if any finding has severity error, else 0."""
    return (
        EXIT_FINDINGS
        if any(finding.severity is Severity.ERROR for finding in findings)
        else EXIT_OK
    )


def echo_findings(findings: Iterable[Finding]) -> int:
    """Print one line per finding and return the exit code for them.

    A command ends with ``raise click.exceptions.Exit(echo_findings(findings))``.
    """
    listed = list(findings)
    for finding in listed:
        where = f" ({finding.file})" if finding.file else ""
        click.echo(f"{finding.severity.value.upper()} {finding.code}: {finding.message}{where}")
    return exit_code_for(listed)


def register_packages(group: click.Group, package: ModuleType = porttool) -> list[str]:
    """Call ``register_cli(group)`` of every subpackage of ``package`` that has a ``cli`` module.

    Returns the names of the subpackages that registered commands. A subpackage whose
    ``cli`` module cannot be imported (a missing optional dependency) is logged and
    skipped: the other commands still work.
    """
    registered: list[str] = []
    for info in sorted(pkgutil.iter_modules(package.__path__), key=lambda item: item.name):
        if not info.ispkg:
            continue
        # Look for the cli module without importing the package: a package without
        # commands (or with heavy imports) costs nothing at start-up.
        folders = [os.path.join(path, info.name) for path in package.__path__]
        if not any(module.name == "cli" for module in pkgutil.iter_modules(folders)):
            continue
        module_name = f"{package.__name__}.{info.name}.cli"
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            _logger.warning(
                "the commands of %s.%s are not available: its cli module cannot be imported",
                package.__name__,
                info.name,
                exc_info=True,
            )
            continue
        register = getattr(module, "register_cli", None)
        if not callable(register):
            _logger.warning("%s has no register_cli(group) function", module_name)
            continue
        register(group)
        registered.append(info.name)
    return registered


def _log_hint() -> str:
    log_file = current_log_file()
    return f"Log file: {log_file}" if log_file is not None else "No log file was written."


class RootGroup(click.Group):
    """Loads the package commands on first use and maps errors to exit codes."""

    _packages_loaded = False

    def _load_packages(self) -> None:
        if not self._packages_loaded:
            self._packages_loaded = True
            register_packages(self)

    def list_commands(self, ctx: click.Context) -> list[str]:
        self._load_packages()
        return super().list_commands(ctx)

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        self._load_packages()
        return super().get_command(ctx, cmd_name)

    def invoke(self, ctx: click.Context) -> Any:
        try:
            setup_logging(
                ctx.params.get("log_dir"),
                console=True,
                console_level=logging.DEBUG if ctx.params.get("verbose") else logging.WARNING,
            )
            install_exception_hooks()
            return super().invoke(ctx)
        except PortToolError as error:
            _logger.info("command failed: %s", error, exc_info=True)  # traceback: file only
            click.echo(str(error), err=True)
            click.echo(_log_hint(), err=True)
            ctx.exit(EXIT_ERROR)
        except (click.exceptions.Exit, click.Abort, click.ClickException):
            raise  # click's own control flow: exit codes and usage errors
        except Exception as error:  # boundary: CLI entry
            _logger.exception("unexpected error in command %r", ctx.invoked_subcommand)
            click.echo(f"Unexpected error: {type(error).__name__}: {error}", err=True)
            click.echo(_log_hint(), err=True)
            ctx.exit(EXIT_UNEXPECTED)


@click.group(cls=RootGroup, context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--log-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Folder of the log file (default: %LOCALAPPDATA%\\PortTool\\logs).",
)
@click.option("-v", "--verbose", is_flag=True, help="Show informational and debug messages.")
@click.version_option(porttool.__version__, prog_name="porttool")
def main(log_dir: Path | None, verbose: bool) -> None:
    """PortTool: AUTOSAR ports, runnable access, triggers and connections from Excel requests."""


if __name__ == "__main__":
    main()
