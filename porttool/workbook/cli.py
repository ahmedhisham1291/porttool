"""Command-line wiring of the workbook package.

Typer is not imported here: Typer builds a command from the plain type hints of the
function, so this module also loads where Typer is missing. The command only parses
its arguments and calls ``import_workbooks``.

The export has no command in this package: it needs a loaded ``Model``, and loading is
the reader's job. The pipeline package owns the command that loads and exports.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, TypeVar

from porttool.core.results import Severity
from porttool.core.sheets import LAYOUT_VERSION, WorkbookMeta
from porttool.workbook.importer import import_workbooks

_F = TypeVar("_F", bound=Callable[..., Any])


class _App(Protocol):
    """The part of ``typer.Typer`` this module uses."""

    def command(self, name: str | None = None) -> Callable[[_F], _F]: ...


def check_workbooks(
    files: list[Path], baseline_commit: str = "", model_hash: str = "", as_json: bool = False
) -> int:
    """Read request workbooks and print what PortTool found in them. Returns the exit code.

    ``baseline_commit`` and ``model_hash`` describe the current project; leave them empty
    to compare each workbook with nothing (a stale-baseline note is then to be expected).
    """
    current = WorkbookMeta(
        tool_version="",
        layout_version=LAYOUT_VERSION,
        autosar_schema=None,
        baseline_commit=baseline_commit or None,
        model_hash=model_hash,
        export_time_utc="",
    )
    imported = import_workbooks(files, current)
    if as_json:
        payload = {
            "rows": [dataclasses.asdict(row) for row in imported.rows],
            "findings": [dataclasses.asdict(finding) for finding in imported.findings],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        for finding in imported.findings:
            print(f"{finding.severity.value.upper():7} {finding.code}  {finding.message}")
        print(f"{len(imported.rows)} request rows, {len(imported.findings)} findings")
    has_error = any(finding.severity is Severity.ERROR for finding in imported.findings)
    return 1 if has_error else 0


def register_cli(app: _App) -> None:
    """Add the workbook commands to the Typer application ``app``."""

    @app.command(name="workbook-check")
    def workbook_check(  # pragma: no cover - thin Typer wrapper, untested without Typer
        files: list[Path], baseline_commit: str = "", model_hash: str = "", as_json: bool = False
    ) -> None:
        """Read request workbooks and print the request rows and findings."""
        raise SystemExit(check_workbooks(files, baseline_commit, model_hash, as_json))
