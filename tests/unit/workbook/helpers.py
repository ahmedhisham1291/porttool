"""Helpers shared by the workbook tests: edit a workbook by script, patch its zip parts."""

from __future__ import annotations

import zipfile
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

import openpyxl

from porttool.core.models import Model
from porttool.core.results import Finding
from porttool.core.sheets import COLUMNS, LAYOUT_VERSION, Sheet, WorkbookMeta

EXPORT_TIME = "2026-11-11T10:00:00Z"


def make_meta(model: Model, **overrides: Any) -> WorkbookMeta:
    fields: dict[str, Any] = {
        "tool_version": "0.0.1",
        "layout_version": LAYOUT_VERSION,
        "autosar_schema": "AUTOSAR_4-2-2.xsd",
        "baseline_commit": model.baseline_commit,
        "model_hash": model.model_hash,
        "export_time_utc": EXPORT_TIME,
    }
    fields.update(overrides)
    return WorkbookMeta(**fields)


def codes_of(findings: Iterable[Finding]) -> list[str]:
    return [finding.code for finding in findings]


def column_of(sheet: Sheet, key: str) -> int:
    """1-based column of ``key`` in an exported sheet."""
    return [column.key for column in COLUMNS[sheet]].index(key) + 1


def header_of(sheet: Sheet, key: str) -> str:
    return next(column.header for column in COLUMNS[sheet] if column.key == key)


def set_cells(worksheet: Any, sheet: Sheet, row: int, **values: Any) -> None:
    """Set cells of ``row`` by column key. None clears the cell."""
    for key, value in values.items():
        worksheet.cell(row=row, column=column_of(sheet, key)).value = value


def data_row_count(worksheet: Any) -> int:
    """Number of rows below the header whose first cell is filled (exported objects)."""
    count = 0
    for (value,) in worksheet.iter_rows(min_row=2, max_col=1, values_only=True):
        if value is None:
            break
        count += 1
    return count


def first_blank_row(worksheet: Any) -> int:
    return data_row_count(worksheet) + 2


def edit(source: Path, target: Path, change: Callable[[Any], None]) -> Path:
    """Load ``source`` with openpyxl, apply ``change`` to the workbook, save as ``target``."""
    workbook = openpyxl.load_workbook(source)
    change(workbook)
    workbook.save(target)
    workbook.close()
    return target


def patch_parts(
    source: Path, target: Path, patches: Mapping[str, Callable[[bytes], bytes] | None]
) -> Path:
    """Copy the zip ``source`` to ``target``; patch the named parts, drop those mapped to None."""
    with zipfile.ZipFile(source) as archive_in:
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive_out:
            for info in archive_in.infolist():
                data = archive_in.read(info.filename)
                if info.filename in patches:
                    patch = patches[info.filename]
                    if patch is None:
                        continue
                    data = patch(data)
                archive_out.writestr(info.filename, data)
    return target


def sheet_part(path: Path, title: str) -> str:
    """Zip part name of the sheet ``title`` (openpyxl resolves the relationships)."""
    workbook = openpyxl.load_workbook(path, read_only=True)
    try:
        worksheet = workbook[title]
        part: str = worksheet._worksheet_path  # test helper only
        return part.lstrip("/")
    finally:
        workbook.close()


def layout_workbook(rows: Mapping[Sheet, Iterable[Mapping[str, Any]]]) -> Any:
    """A workbook written by openpyxl alone: layout headers, the given rows, nothing else.

    Stands in for a file that went through LibreOffice or Google Sheets: no defined
    names, no protection, other styles, no Meta sheet.
    """
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    for sheet, entries in rows.items():
        worksheet = workbook.create_sheet(sheet.value)
        worksheet.append([column.header for column in COLUMNS[sheet]])
        for entry in entries:
            worksheet.append([entry.get(column.key) for column in COLUMNS[sheet]])
    return workbook
