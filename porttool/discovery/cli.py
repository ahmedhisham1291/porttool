"""Command line of discovery: ``porttool discover run`` and ``porttool discover golden-diff``.

Thin on purpose: parse arguments, call the tested functions, print the summary.
Exit code 0 when the report was written (even with findings), 2 when it was not.
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback
from collections.abc import Callable

import click

from porttool.core.errors import PortToolError
from porttool.discovery.fsio import now_utc
from porttool.discovery.runner import (
    DiscoveryOptions,
    DiscoveryResult,
    GoldenDiffOptions,
    run_discovery,
    run_golden_diff,
)

_REPO_HELP = "Root folder of the ECU repo."
_OUT_HELP = (
    "Report folder. Default: <repo>/porttool_discovery. Must be new, empty or a folder "
    "written by an earlier discover command."
)
_ANONYMIZE_HELP = (
    "Replace every short name, file name and folder name by a stable token. A private "
    "folder next to the report folder keeps the key and the token table."
)
_GIT_HELP = "Path to git.exe if git is not on PATH."


def safe_echo(text: str) -> None:
    """Print text even when the console cannot show every character."""
    stream = sys.stdout
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        shown = text.encode(encoding, errors="backslashreplace").decode(encoding)
    except LookupError:
        shown = text.encode("ascii", errors="backslashreplace").decode("ascii")
    click.echo(shown)


def _crash_log(error: BaseException) -> str:
    """Write the traceback of an unexpected failure to a log file and return its path."""
    base = os.environ.get("LOCALAPPDATA")
    folder = os.path.join(base, "PortTool", "logs") if base else tempfile.gettempdir()
    name = "discover_crash_" + now_utc().replace(":", "").replace("-", "") + ".log"
    text = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    for candidate in (folder, tempfile.gettempdir()):
        path = os.path.join(candidate, name)
        try:
            os.makedirs(candidate, exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
        except OSError:
            continue
        return path
    return "(the log file could not be written)"


def _execute(action: Callable[[], DiscoveryResult]) -> None:
    """CLI boundary: a clear message and exit code 2 instead of a traceback."""
    try:
        result = action()
    except PortToolError as error:
        safe_echo(f"ERROR {error}")
        raise click.exceptions.Exit(2) from error
    except Exception as error:  # noqa: BLE001 - CLI entry: log the traceback, show a message
        path = _crash_log(error)
        safe_echo(
            f"ERROR discovery stopped unexpectedly ({type(error).__name__}). "
            f"Please send this log file: {path}"
        )
        raise click.exceptions.Exit(2) from error
    safe_echo(result.summary)


@click.group("discover")
def discover() -> None:
    """Study the real ECU repo and write a report to send back. Changes nothing."""


@discover.command("run")
@click.option("--repo", required=True, type=click.Path(), help=_REPO_HELP)
@click.option("--dpa", type=click.Path(), help="The DaVinci Configurator project file (.dpa).")
@click.option("--sip", type=click.Path(), help="Folder of the Vector SIP (holds DVCfgCmd).")
@click.option(
    "--developer-bin", type=click.Path(), help="Bin folder of DaVinci Developer (DVImEx, ...)."
)
@click.option("--out", type=click.Path(), help=_OUT_HELP)
@click.option("--anonymize", is_flag=True, help=_ANONYMIZE_HELP)
@click.option("--no-tools", is_flag=True, help="Do not search for or start any Vector tool.")
@click.option(
    "--exclude",
    multiple=True,
    help="Glob of files or folders to skip, relative to the repo root. Repeatable.",
)
@click.option("--git", "git_executable", default="git", show_default=True, help=_GIT_HELP)
def run_command(
    repo: str,
    dpa: str | None,
    sip: str | None,
    developer_bin: str | None,
    out: str | None,
    anonymize: bool,
    no_tools: bool,
    exclude: tuple[str, ...],
    git_executable: str,
) -> None:
    """Gather the facts about the repo, the workspace files and the Vector tools.

    Read-only. Vector tools are started with a help flag only, never with a project.
    """
    options = DiscoveryOptions(
        repo=repo,
        dpa=dpa,
        sip=sip,
        developer_bin=developer_bin,
        out=out,
        anonymize=anonymize,
        no_tools=no_tools,
        exclude=tuple(exclude),
        git_executable=git_executable,
    )
    _execute(lambda: run_discovery(options, safe_echo))


@discover.command("golden-diff")
@click.option("--repo", required=True, type=click.Path(), help=_REPO_HELP)
@click.option("--base", required=True, help="Commit before the hand-made reference change.")
@click.option("--head", required=True, help="Commit after the hand-made reference change.")
@click.option("--out", type=click.Path(), help=_OUT_HELP)
@click.option("--anonymize", is_flag=True, help=_ANONYMIZE_HELP)
@click.option("--git", "git_executable", default="git", show_default=True, help=_GIT_HELP)
def golden_diff_command(
    repo: str, base: str, head: str, out: str | None, anonymize: bool, git_executable: str
) -> None:
    """Compare two commits element by element (the hand-made reference changes)."""
    options = GoldenDiffOptions(
        repo=repo,
        base=base,
        head=head,
        out=out,
        anonymize=anonymize,
        git_executable=git_executable,
    )
    _execute(lambda: run_golden_diff(options, safe_echo))


def register_cli(group: click.Group) -> None:
    """Add the ``discover`` command group to the application's click group."""
    group.add_command(discover)
