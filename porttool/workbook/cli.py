"""Command-line wiring of the workbook package (click).

``register_cli(app)`` adds ``workbook-check`` to the application's click group. The
command only parses its arguments and calls ``check_workbooks``.

The export has no command in this package: it needs a loaded ``Model``, and loading is
the reader's job. The pipeline package owns the command that loads and exports.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Sequence
from pathlib import Path

import click

from porttool.core.results import Severity
from porttool.core.sheets import LAYOUT_VERSION, WorkbookMeta
from porttool.workbook.importer import import_workbooks


def check_workbooks(
    files: Sequence[Path], baseline_commit: str = "", model_hash: str = "", as_json: bool = False
) -> int:
    """Read request workbooks and print what PortTool found in them. Returns the exit code.

    ``baseline_commit`` and ``model_hash`` describe the current project. Without them
    every workbook is reported as exported from another state of the project.
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
        click.echo(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        for finding in imported.findings:
            click.echo(f"{finding.severity.value.upper():7} {finding.code}  {finding.message}")
        click.echo(f"{len(imported.rows)} request rows, {len(imported.findings)} findings")
    has_error = any(finding.severity is Severity.ERROR for finding in imported.findings)
    return 1 if has_error else 0


@click.command(name="workbook-check")
@click.argument("files", nargs=-1, required=True, type=click.Path(path_type=Path))
@click.option("--baseline-commit", default="", help="Commit of the current project state.")
@click.option("--model-hash", default="", help="Model hash of the current project state.")
@click.option("--json", "as_json", is_flag=True, help="Print rows and findings as JSON.")
def workbook_check(
    files: tuple[Path, ...], baseline_commit: str, model_hash: str, as_json: bool
) -> None:
    """Read request workbooks and print the request rows and findings.

    Exit code 1 when a finding of severity error was reported.
    """
    raise SystemExit(check_workbooks(files, baseline_commit, model_hash, as_json))


def register_cli(app: click.Group) -> None:
    """Add the workbook commands to the application's click group."""
    app.add_command(workbook_check)
