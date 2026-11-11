"""Excel export with XlsxWriter.

``export_workbook`` is a blocking function. Expected problems (missing folder, locked
target, full disk, too many rows) come back as findings; nothing is raised for them.
The file is written to a temporary file in the target folder and moved over the target
with ``os.replace``, so a reader never sees a half-written workbook.
"""

from __future__ import annotations

import dataclasses
import datetime
import errno
import os
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Final

import xlsxwriter
from xlsxwriter.exceptions import FileCreateError, XlsxWriterException
from xlsxwriter.utility import quote_sheetname, xl_col_to_name

from porttool.core.config import ProjectConfig
from porttool.core.errors import WorkbookError
from porttool.core.models import Model
from porttool.core.results import Finding, RequestResult, RowRef, Severity
from porttool.core.sheets import COLUMNS, REQUEST_SHEETS, Sheet, WorkbookMeta
from porttool.workbook import codes
from porttool.workbook.layout import (
    EXCEL_MAX_CELL_CHARS,
    EXCEL_MAX_ROWS,
    META_HEADERS,
    SHEET_ORDER,
    list_range_name,
)
from porttool.workbook.lists import ENUM_LISTS, build_lists
from porttool.workbook.readme import ReadmeLine, Style, readme_lines
from porttool.workbook.rows import SheetRows, build_all_rows, build_labels

#: XlsxWriter stamps this as creation and modification time. Fixed, so that two exports
#: of the same model are byte-identical.
_FIXED_CREATED: Final = datetime.datetime(2020, 1, 1, 0, 0, 0)

_COLOR_HEADER: Final = "#1F4E78"
_COLOR_REQUEST_HEADER: Final = "#C55A11"
_COLOR_EDITABLE: Final = "#FFF2CC"
_COLOR_LOCKED: Final = "#F2F2F2"
_COLOR_GRID: Final = "#BFBFBF"
_COLOR_REQUEST_TAB: Final = "#70AD47"

_MIN_WIDTH: Final = 8
_MAX_WIDTH: Final = 60
_WIDTH_PADDING: Final = 3  # room for the filter arrow

#: What a user may still do on a protected sheet. Everything else is refused by Excel.
_PROTECTION: Final[Mapping[str, bool]] = {
    "autofilter": True,
    "format_columns": True,
    "format_rows": True,
    "select_locked_cells": True,
    "select_unlocked_cells": True,
}


@dataclass(frozen=True, slots=True)
class ExportOptions:
    """Knobs of the export. The defaults are the product behaviour."""

    #: Blank, unlocked rows below the data of each request sheet.
    blank_rows: int = 500
    #: Row limit of one sheet, header included. Lower it only in tests.
    max_rows: int = EXCEL_MAX_ROWS
    #: Character limit of one cell. Lower it only in tests.
    max_cell_chars: int = EXCEL_MAX_CELL_CHARS
    #: How many compatible partner ports the Unconnected sheet names per port.
    partner_limit: int = 10
    #: Waits between the attempts to replace a target that reports a sharing violation.
    retry_delays_s: tuple[float, ...] = (0.1, 0.2, 0.4)


@dataclass(frozen=True, slots=True)
class _Formats:
    header: Any
    request_header: Any
    text_locked: Any
    number_locked: Any
    editable: Any
    readme_title: Any
    readme_heading: Any
    readme_label: Any
    readme_text: Any
    legend_editable: Any
    legend_locked: Any


def _check(status: int, what: str) -> None:
    """XlsxWriter reports a refused call by a negative return value, not by an exception."""
    if status:
        raise WorkbookError(detail=f"XlsxWriter refused {what} (status {status}).")


def _add_formats(workbook: Any) -> _Formats:
    header = {"bold": True, "font_color": "#FFFFFF", "border": 1, "border_color": _COLOR_GRID}
    editable = {
        "num_format": "@",
        "locked": False,
        "bg_color": _COLOR_EDITABLE,
        "border": 1,
        "border_color": _COLOR_GRID,
    }
    wrap = {"text_wrap": True, "valign": "top"}
    return _Formats(
        header=workbook.add_format({**header, "bg_color": _COLOR_HEADER}),
        request_header=workbook.add_format({**header, "bg_color": _COLOR_REQUEST_HEADER}),
        text_locked=workbook.add_format({"num_format": "@"}),
        number_locked=workbook.add_format({"align": "left"}),
        editable=workbook.add_format(editable),
        readme_title=workbook.add_format({"bold": True, "font_size": 16}),
        readme_heading=workbook.add_format({"bold": True, "font_size": 12, "bottom": 1}),
        readme_label=workbook.add_format({**wrap, "bold": True, "num_format": "@"}),
        readme_text=workbook.add_format({**wrap, "num_format": "@"}),
        legend_editable=workbook.add_format(
            {"bg_color": _COLOR_EDITABLE, "border": 1, "border_color": _COLOR_GRID}
        ),
        legend_locked=workbook.add_format(
            {"bg_color": _COLOR_LOCKED, "border": 1, "border_color": _COLOR_GRID}
        ),
    )


def _clip(
    text: str, limit: int, where: RowRef, object_path: str | None, findings: list[Finding]
) -> str:
    if len(text) <= limit:
        return text
    findings.append(
        Finding(
            code=codes.EXPORT_CELL_TRUNCATED,
            severity=Severity.WARNING,
            message=(
                f"A cell on sheet '{where.sheet}', row {where.row}, holds {len(text)} "
                f"characters. Excel allows {limit}; the text was cut."
            ),
            object_path=object_path,
            file=where.file,
            row=where,
        )
    )
    return text[:limit]


def _validation(list_name: str) -> dict[str, Any]:
    options: dict[str, Any] = {
        "validate": "list",
        "source": f"={list_range_name(list_name)}",
        "ignore_blank": True,
        "dropdown": True,
    }
    if list_name in ENUM_LISTS:
        options.update(
            error_type="stop",
            error_title="Value not allowed",
            error_message="Pick a value from the list.",
        )
    else:
        options.update(
            error_type="information",
            error_title="Name not in the project",
            error_message=(
                "This name is not in the exported project. Keep it only if another row "
                "of this workbook creates it."
            ),
        )
    return options


def _write_data_sheet(
    workbook: Any,
    data: SheetRows,
    blank_rows: int,
    formats: _Formats,
    options: ExportOptions,
    file: str,
    findings: list[Finding],
) -> None:
    sheet = data.sheet
    columns = COLUMNS[sheet]
    column_count = len(columns)
    is_request_sheet = sheet in REQUEST_SHEETS
    editable = [is_request_sheet and column.editable for column in columns]
    widths = [len(column.header) for column in columns]
    worksheet = workbook.add_worksheet(sheet.value)

    write_string = worksheet.write_string
    write_number = worksheet.write_number
    write_blank = worksheet.write_blank
    text_locked = formats.text_locked
    number_locked = formats.number_locked
    unlocked = formats.editable
    limit = options.max_cell_chars

    for col, column in enumerate(columns):
        header_format = formats.request_header if column.editable else formats.header
        _check(write_string(0, col, column.header, header_format), "a header cell")

    status = 0
    for row_index, values in enumerate(data.rows, start=1):
        col = 0
        for value in values:
            if value == "":
                if editable[col]:
                    status |= write_blank(row_index, col, None, unlocked)
            elif type(value) is str:
                size = len(value)
                if size > limit:
                    where = RowRef(file=file, sheet=sheet.value, row=row_index + 1)
                    value = _clip(value, limit, where, data.paths[row_index - 1], findings)
                    size = limit
                if size > widths[col]:
                    widths[col] = size
                cell_format = unlocked if editable[col] else text_locked
                status |= write_string(row_index, col, value, cell_format)
            else:
                status |= write_number(row_index, col, value, number_locked)
            col += 1
    _check(status, f"a cell on sheet {sheet.value}")

    last_row = len(data.rows)
    if is_request_sheet:
        for row_index in range(last_row + 1, last_row + 1 + blank_rows):
            for col in range(column_count):
                status |= write_blank(row_index, col, None, unlocked)
        _check(status, f"a blank row on sheet {sheet.value}")
        last_row += blank_rows
        worksheet.set_tab_color(_COLOR_REQUEST_TAB)
        if last_row >= 1:
            for col, column in enumerate(columns):
                if column.dropdown is not None:
                    _check(
                        worksheet.data_validation(
                            1, col, last_row, col, _validation(column.dropdown)
                        ),
                        f"the dropdown of column {column.header}",
                    )

    for col, width in enumerate(widths):
        clamped = min(max(width + _WIDTH_PADDING, _MIN_WIDTH), _MAX_WIDTH)
        _check(worksheet.set_column(col, col, clamped), "a column width")
    worksheet.freeze_panes(1, 0)
    worksheet.autofilter(0, 0, last_row, column_count - 1)
    # New entries are stored as text on purpose; Excel shall not flag every number.
    _check(
        worksheet.ignore_errors({"number_stored_as_text": "A1:XFD1048576"}),
        "the error-check setting",
    )
    worksheet.protect("", dict(_PROTECTION))


def _write_readme(workbook: Any, lines: Sequence[ReadmeLine], formats: _Formats) -> None:
    worksheet = workbook.add_worksheet(Sheet.README.value)
    _check(worksheet.set_column(0, 0, 30), "a column width")
    _check(worksheet.set_column(1, 1, 110), "a column width")
    legend: Mapping[Style, Any] = {
        Style.LEGEND_EDITABLE: formats.legend_editable,
        Style.LEGEND_LOCKED: formats.legend_locked,
        Style.LEGEND_HEADER: formats.header,
        Style.LEGEND_REQUEST_HEADER: formats.request_header,
    }
    status = 0
    for row_index, line in enumerate(lines):
        if line.style is Style.TITLE:
            status |= worksheet.write_string(row_index, 0, line.left, formats.readme_title)
        elif line.style is Style.HEADING:
            status |= worksheet.write_string(row_index, 0, line.left, formats.readme_heading)
            status |= worksheet.write_blank(row_index, 1, None, formats.readme_heading)
        elif line.style in legend:
            status |= worksheet.write_blank(row_index, 0, None, legend[line.style])
            status |= worksheet.write_string(row_index, 1, line.right, formats.readme_text)
        else:
            if line.left:
                status |= worksheet.write_string(row_index, 0, line.left, formats.readme_label)
            status |= worksheet.write_string(row_index, 1, line.right, formats.readme_text)
    _check(status, "a README cell")
    worksheet.hide_gridlines(2)
    worksheet.protect("", {"format_columns": True, "format_rows": True})


def _write_lists(
    workbook: Any,
    lists: Mapping[str, tuple[str, ...]],
    formats: _Formats,
    options: ExportOptions,
    file: str,
    findings: list[Finding],
) -> None:
    """One column per list; a defined name per column feeds the data validations."""
    worksheet = workbook.add_worksheet(Sheet.LISTS.value)
    sheet_ref = quote_sheetname(Sheet.LISTS.value)
    status = 0
    for col, (name, entries) in enumerate(lists.items()):
        status |= worksheet.write_string(0, col, name, formats.header)
        for row_index, entry in enumerate(entries, start=1):
            where = RowRef(file=file, sheet=Sheet.LISTS.value, row=row_index + 1)
            text = _clip(entry, options.max_cell_chars, where, None, findings)
            status |= worksheet.write_string(row_index, col, text, formats.text_locked)
        letter = xl_col_to_name(col, True)
        last = max(len(entries), 1) + 1  # an empty list still needs one (blank) cell
        _check(
            workbook.define_name(list_range_name(name), f"={sheet_ref}!{letter}$2:{letter}${last}"),
            f"the defined name of list {name}",
        )
        _check(worksheet.set_column(col, col, 28), "a column width")
    _check(status, "a Lists cell")
    worksheet.freeze_panes(1, 0)
    worksheet.protect("", {})
    worksheet.hide()


def _write_meta(workbook: Any, meta: WorkbookMeta, formats: _Formats) -> None:
    worksheet = workbook.add_worksheet(Sheet.META.value)
    status = worksheet.write_string(0, 0, META_HEADERS[0], formats.header)
    status |= worksheet.write_string(0, 1, META_HEADERS[1], formats.header)
    for row_index, field in enumerate(dataclasses.fields(meta), start=1):
        value = getattr(meta, field.name)
        status |= worksheet.write_string(row_index, 0, field.name, formats.text_locked)
        text = "" if value is None else str(value)
        status |= worksheet.write_string(row_index, 1, text, formats.text_locked)
    _check(status, "a Meta cell")
    _check(worksheet.set_column(0, 0, 22), "a column width")
    _check(worksheet.set_column(1, 1, 70), "a column width")
    worksheet.protect("", {})
    worksheet.hide()


def _write_workbook(
    handle: IO[bytes],
    sheets: Mapping[Sheet, SheetRows],
    blank_rows: Mapping[Sheet, int],
    lists: Mapping[str, tuple[str, ...]],
    meta: WorkbookMeta,
    options: ExportOptions,
    file: str,
    findings: list[Finding],
) -> None:
    workbook = xlsxwriter.Workbook(
        handle,
        {
            "in_memory": True,  # no XlsxWriter temp files; zip entries get a fixed time
            "strings_to_numbers": False,
            "strings_to_formulas": False,
            "strings_to_urls": False,
        },
    )
    workbook.set_properties(
        {"title": "PortTool request workbook", "author": "PortTool", "created": _FIXED_CREATED}
    )
    formats = _add_formats(workbook)
    for sheet in SHEET_ORDER:
        if sheet is Sheet.README:
            _write_readme(workbook, readme_lines(meta, options.blank_rows), formats)
        elif sheet is Sheet.LISTS:
            _write_lists(workbook, lists, formats, options, file, findings)
        elif sheet is Sheet.META:
            _write_meta(workbook, meta, formats)
        else:
            _write_data_sheet(
                workbook, sheets[sheet], blank_rows.get(sheet, 0), formats, options, file, findings
            )
    workbook.close()


# --------------------------------------------------------------------------- limits


def _plan_blank_rows(
    sheets: Mapping[Sheet, SheetRows],
    lists: Mapping[str, tuple[str, ...]],
    options: ExportOptions,
    file: str,
    findings: list[Finding],
) -> dict[Sheet, int] | None:
    """Blank rows per request sheet, or None when a sheet cannot hold its rows."""
    counts = {sheet: len(data.rows) for sheet, data in sheets.items()}
    counts[Sheet.LISTS] = max((len(entries) for entries in lists.values()), default=0)
    fits = True
    plan: dict[Sheet, int] = {}
    for sheet in SHEET_ORDER:
        if sheet not in counts:
            continue
        needed = counts[sheet] + 1  # header row
        if needed > options.max_rows:
            fits = False
            findings.append(
                Finding(
                    code=codes.EXPORT_TOO_MANY_ROWS,
                    severity=Severity.ERROR,
                    message=(
                        f"Sheet '{sheet.value}' needs {needed} rows. Excel allows "
                        f"{options.max_rows} rows per sheet. No workbook was written."
                    ),
                    file=file,
                )
            )
        elif sheet in REQUEST_SHEETS:
            blank = min(options.blank_rows, options.max_rows - needed)
            plan[sheet] = blank
            if blank < options.blank_rows:
                findings.append(
                    Finding(
                        code=codes.EXPORT_BLANK_ROWS_REDUCED,
                        severity=Severity.WARNING,
                        message=(
                            f"Sheet '{sheet.value}' has room for {blank} blank rows only, "
                            f"not {options.blank_rows}: Excel allows {options.max_rows} "
                            f"rows per sheet."
                        ),
                        file=file,
                    )
                )
    return plan if fits else None


# --------------------------------------------------------------------------- saving


def _open_temp(target: Path) -> tuple[IO[bytes], Path]:
    """Create the temporary file next to ``target``. Seam for tests."""
    descriptor, name = tempfile.mkstemp(dir=target.parent, prefix=f"{target.stem}.", suffix=".tmp")
    try:
        return os.fdopen(descriptor, "wb"), Path(name)
    except OSError:
        os.close(descriptor)
        raise


def _replace(source: Path, target: Path) -> None:
    """Atomic move. Seam for tests; on Windows a target open in Excel raises PermissionError."""
    os.replace(source, target)


def _os_finding(error: OSError, target: Path) -> Finding:
    """Map an operating-system error that hit the temporary file to a finding."""
    detail = error.strerror or str(error)
    if error.errno == errno.ENOSPC:
        code = codes.EXPORT_DISK_FULL
        message = f"The disk is full. The workbook '{target.name}' was not written."
    elif isinstance(error, PermissionError):
        code = codes.EXPORT_FOLDER_READ_ONLY
        message = (
            f"PortTool may not write into the folder '{target.parent}' ({detail}). "
            f"The workbook was not written."
        )
    elif isinstance(error, FileNotFoundError):
        code = codes.EXPORT_FOLDER_MISSING
        message = f"The folder '{target.parent}' does not exist. The workbook was not written."
    else:
        code = codes.EXPORT_IO_ERROR
        message = f"The workbook '{target.name}' could not be written: {detail}."
    return Finding(code=code, severity=Severity.ERROR, message=message, file=str(target))


def _replace_with_retry(
    temp: Path, target: Path, delays: Sequence[float], sleep: Callable[[float], None]
) -> Finding | None:
    """Move ``temp`` over ``target``; wait and try again while the target is locked."""
    waits: list[float | None] = [*delays, None]
    for wait in waits:
        try:
            _replace(temp, target)
        except PermissionError as error:
            if wait is None:
                return Finding(
                    code=codes.EXPORT_TARGET_LOCKED,
                    severity=Severity.ERROR,
                    message=(
                        f"'{target.name}' is open in Excel or is read-only "
                        f"({error.strerror or error}). Close it and export again. The "
                        f"existing file was left unchanged."
                    ),
                    file=str(target),
                )
            sleep(wait)
        except OSError as error:
            return _os_finding(error, target)
        else:
            return None
    return None  # pragma: no cover - the loop always returns


def _remove_temp(temp: Path, target: Path) -> Finding | None:
    try:
        os.unlink(temp)
    except FileNotFoundError:
        return None  # moved over the target: the normal case
    except OSError as error:
        return Finding(
            code=codes.EXPORT_TEMP_LEFT_BEHIND,
            severity=Severity.WARNING,
            message=(
                f"The temporary file '{temp.name}' could not be removed "
                f"({error.strerror or error}). Delete it by hand."
            ),
            file=str(target),
        )
    return None


def _save(
    target: Path,
    write: Callable[[IO[bytes]], None],
    options: ExportOptions,
    sleep: Callable[[float], None],
) -> list[Finding]:
    """Write through a temporary file in the target folder, then replace the target."""
    try:
        handle, temp = _open_temp(target)
    except OSError as error:
        return [_os_finding(error, target)]
    findings: list[Finding] = []
    try:
        try:
            with handle:
                write(handle)
                handle.flush()
                os.fsync(handle.fileno())
        except FileCreateError as error:
            cause = error.args[0] if error.args else None
            if not isinstance(cause, OSError):
                raise WorkbookError(detail=str(error)) from error
            findings.append(_os_finding(cause, target))
        except XlsxWriterException as error:
            raise WorkbookError(detail=f"{type(error).__name__}: {error}") from error
        except OSError as error:
            findings.append(_os_finding(error, target))
        else:
            locked = _replace_with_retry(temp, target, options.retry_delays_s, sleep)
            if locked is not None:
                findings.append(locked)
    finally:
        leftover = _remove_temp(temp, target)
        if leftover is not None:
            findings.append(leftover)
    return findings


# --------------------------------------------------------------------------- entry point


def export_workbook(
    model: Model,
    config: ProjectConfig,
    target: Path,
    meta: WorkbookMeta,
    results: Sequence[RequestResult] = (),
    options: ExportOptions | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[Finding, ...]:
    """Write the request workbook for ``model`` to ``target``. Blocking.

    Returns the findings of the export. An ERROR finding means no file was written and
    an existing ``target`` is unchanged.
    """
    options = options or ExportOptions()
    file = str(target)
    if target.suffix.lower() != ".xlsx":
        return (
            Finding(
                code=codes.EXPORT_BAD_EXTENSION,
                severity=Severity.ERROR,
                message=f"The workbook must be an .xlsx file, not '{target.name}'.",
                file=file,
            ),
        )
    if not target.parent.is_dir():
        return (
            Finding(
                code=codes.EXPORT_FOLDER_MISSING,
                severity=Severity.ERROR,
                message=(
                    f"The folder '{target.parent}' does not exist. The workbook was not written."
                ),
                file=file,
            ),
        )
    labels = build_labels(model)
    sheets = build_all_rows(model, config, results, labels, options.partner_limit)
    lists = build_lists(model, labels)
    findings: list[Finding] = []
    blank_rows = _plan_blank_rows(sheets, lists, options, file, findings)
    if blank_rows is None:
        return tuple(findings)

    def write(handle: IO[bytes]) -> None:
        _write_workbook(handle, sheets, blank_rows, lists, meta, options, file, findings)

    cell_findings = len(findings)
    save_findings = _save(target, write, options, sleep)
    if any(finding.severity is Severity.ERROR for finding in save_findings):
        # Nothing was written: findings about cells of that file would only confuse.
        del findings[cell_findings:]
    findings.extend(save_findings)
    return tuple(findings)
