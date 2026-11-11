"""The workbook-check command."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click
from click.testing import CliRunner

from porttool.core.sheets import Sheet, WorkbookMeta
from porttool.workbook import codes, register_cli
from tests.unit.workbook.helpers import edit, set_cells


def app() -> click.Group:
    group = click.Group(name="porttool")
    register_cli(group)
    return group


def test_register_cli_adds_the_workbook_check_command() -> None:
    assert list(app().commands) == ["workbook-check"]


def test_workbook_check_prints_findings_and_fails_on_an_error(
    exported: Path, meta: WorkbookMeta
) -> None:
    def change(workbook: Any) -> None:
        set_cells(workbook["Ports"], Sheet.PORTS, 2, action="DELETE")
        set_cells(workbook["Ports"], Sheet.PORTS, 3, action="remove")

    path = edit(exported, exported.with_name("edited.xlsx"), change)
    baseline = ["--baseline-commit", str(meta.baseline_commit), "--model-hash", meta.model_hash]
    result = CliRunner().invoke(app(), ["workbook-check", str(path), *baseline])
    assert result.exit_code == 1
    assert f"ERROR   {codes.ROW_ACTION_UNKNOWN}  Sheet 'Ports', row 3:" in result.output
    assert "2 request rows, 1 findings" in result.output

    clean = CliRunner().invoke(app(), ["workbook-check", str(exported), *baseline])
    assert clean.exit_code == 0
    assert clean.output.strip() == "0 request rows, 0 findings"


def test_workbook_check_without_baseline_reports_a_stale_workbook_as_json(
    exported: Path,
) -> None:
    def change(workbook: Any) -> None:
        set_cells(workbook["Ports"], Sheet.PORTS, 2, action="DELETE", comment="Größe")

    path = edit(exported, exported.with_name("edited.xlsx"), change)
    result = CliRunner().invoke(app(), ["workbook-check", "--json", str(path)])
    assert result.exit_code == 0  # a stale baseline is a warning, not an error
    payload = json.loads(result.output)
    assert [finding["code"] for finding in payload["findings"]] == [codes.META_STALE_BASELINE]
    assert payload["findings"][0]["severity"] == "warning"
    row = payload["rows"][0]
    assert (row["action"], row["comment"], row["row"]["row"]) == ("DELETE", "Größe", 2)
    assert row["values"]["port"] == "PpSpeed"


def test_workbook_check_needs_a_file() -> None:
    result = CliRunner().invoke(app(), ["workbook-check"])
    assert result.exit_code == 2
    assert "Missing argument" in result.output
