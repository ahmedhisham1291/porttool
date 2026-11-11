"""Excel import with openpyxl (``read_only=True, data_only=True``).

``import_workbooks`` is a blocking function. It turns the edited rows of one or more
request workbooks into ``RawRequestRow`` objects and reports everything odd about the
files as findings. It judges no AUTOSAR rule: that is the validator's job.

What counts as a request row:

* a row with an Action, whatever else it holds, and
* a row without RowID that has any content (a new row; without Action it also gets a
  finding).

A row with a finding of severity ERROR is still returned, so that nothing a user typed
disappears. The finding carries the same ``RowRef`` as the row.
"""

from __future__ import annotations

import dataclasses
import datetime
import io
import os
import posixpath
import re
import time
import xml.etree.ElementTree as ET
import zipfile
import zlib
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import openpyxl
from openpyxl.utils.exceptions import InvalidFileException

from porttool.core.interfaces import ImportedWorkbooks
from porttool.core.results import Finding, RawRequestRow, RowRef, Severity
from porttool.core.sheets import COLUMNS, REQUEST_SHEETS, Action, Column, Sheet, WorkbookMeta
from porttool.workbook import codes
from porttool.workbook.layout import (
    KEY_ACTION,
    KEY_COMMENT,
    KEY_REQUESTER,
    KEY_ROW_ID,
    OPTIONAL_KEYS,
    REQUEST_KEYS,
    row_id_pattern,
)

_OLE_MAGIC: Final = bytes.fromhex("D0CF11E0A1B11AE1")  # encrypted .xlsx and binary .xls
_ZIP_MAGIC: Final = b"PK\x03\x04"
_ZIP_ENCRYPTED: Final = 0x1  # general-purpose flag bit 0 of a zip entry
_LOCK_FILE_PREFIX: Final = "~$"

#: Non-breaking spaces Excel and web pages put into pasted text.
_SPACES: Final = str.maketrans({" ": " ", " ": " ", " ": " "})

_ACTIONS: Final[frozenset[str]] = frozenset(member.value for member in Action)

_DATE_TYPES: Final = (datetime.datetime, datetime.date, datetime.time, datetime.timedelta)

#: What openpyxl, zipfile and the XML parsers raise on damaged content. SyntaxError is
#: the base class of the ParseError of both ElementTree and lxml.
_DAMAGE_ERRORS: Final = (
    zipfile.BadZipFile,
    zlib.error,
    EOFError,
    InvalidFileException,
    KeyError,
    IndexError,
    ValueError,
    TypeError,
    AttributeError,
    SyntaxError,
)

_FORMULA_TAG: Final = re.compile(rb"<(?:[A-Za-z_][\w.\-]*:)?f[\s>/]")
_MERGE_REF: Final = re.compile(
    rb'<(?:[A-Za-z_][\w.\-]*:)?mergeCell\b[^>]*?\bref="([A-Z]{1,3})([0-9]+):([A-Z]{1,3})([0-9]+)"'
)

_UNSUPPORTED: Final[Mapping[str, str]] = {
    ".xls": "an old binary Excel file (.xls). Open it in Excel and save it as Excel "
    "Workbook (.xlsx)",
    ".xlsm": "a macro-enabled workbook (.xlsm). Save it as Excel Workbook (.xlsx)",
    ".csv": "a CSV file. PortTool reads only the .xlsx workbook it exported",
}


class _Damaged(Exception):
    """The workbook content cannot be read. Private signal, never leaves this module."""


@dataclass(frozen=True, slots=True)
class _Scan:
    """What the raw sheet XML shows and openpyxl's read-only mode hides."""

    formulas: tuple[tuple[int, int, bool], ...]  # (row, column, has stored value), 1-based
    merges: tuple[tuple[int, int, int, int], ...]  # (first row, first col, last row, last col)


# --------------------------------------------------------------------------- cell text


def clean_text(text: str) -> str:
    """Non-breaking spaces become spaces; leading and trailing white space goes."""
    return text.translate(_SPACES).strip()


def number_text(value: float) -> str:
    """A number as plain text: no trailing ``.0``, at most 15 significant digits."""
    if value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    return format(value, ".15g")


def cell_text(value: object) -> str:
    """The text of a cell value as openpyxl returns it."""
    if value is None:
        return ""
    if type(value) is str:
        return clean_text(value)
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return number_text(value)
    if isinstance(value, datetime.datetime | datetime.date | datetime.time):
        return value.isoformat()
    return clean_text(str(value))


def _header_key(value: object) -> str:
    """Header text for matching: trimmed, inner white space collapsed, case ignored."""
    return " ".join(cell_text(value).split()).casefold()


def _column_number(letters: str) -> int:
    number = 0
    for letter in letters:
        number = number * 26 + (ord(letter) - 64)
    return number


def _column_letters(number: int) -> str:
    letters = ""
    while number > 0:
        number, remainder = divmod(number - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


# --------------------------------------------------------------------------- raw scan


def _local(tag: str) -> str:
    return tag.rpartition("}")[2]


def _sheet_parts(archive: zipfile.ZipFile) -> dict[str, str]:
    """Map sheet titles to their XML part inside the package. Empty when not resolvable."""
    try:
        workbook_xml = ET.fromstring(archive.read("xl/workbook.xml"))
        relations_xml = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    except (KeyError, ET.ParseError, zipfile.BadZipFile, zlib.error):
        return {}
    targets = {
        relation.get("Id"): relation.get("Target")
        for relation in relations_xml.iter()
        if _local(relation.tag) == "Relationship"
    }
    parts: dict[str, str] = {}
    for element in workbook_xml.iter():
        if _local(element.tag) != "sheet":
            continue
        title = element.get("name")
        relation_id = next(
            (value for name, value in element.attrib.items() if _local(name) == "id"), None
        )
        target = targets.get(relation_id)
        if title is None or not target:
            continue
        if target.startswith("/"):
            parts[title] = target.lstrip("/")
        else:
            parts[title] = posixpath.normpath(posixpath.join("xl", target))
    return parts


def _formula_cells(xml: bytes) -> tuple[tuple[int, int, bool], ...]:
    """Every cell of the sheet that holds a formula. Only called when one exists."""
    found: list[tuple[int, int, bool]] = []
    row_number = 0
    column_number = 0
    for event, element in ET.iterparse(io.BytesIO(xml), events=("start", "end")):
        tag = _local(element.tag)
        if event == "start":
            if tag == "row":
                explicit = element.get("r")
                row_number = int(explicit) if explicit else row_number + 1
                column_number = 0
            continue
        if tag == "c":
            reference = element.get("r")
            if reference:
                column_number = _column_number(reference.rstrip("0123456789"))
            else:
                column_number += 1
            has_formula = False
            has_value = False
            for child in element:
                child_tag = _local(child.tag)
                if child_tag == "f":
                    has_formula = True
                elif child_tag == "v":
                    has_value = bool(child.text)
                elif child_tag == "is":
                    has_value = True
            if has_formula:
                found.append((row_number, column_number, has_value))
        elif tag == "row":
            element.clear()
        elif tag == "sheetData":
            break
    return tuple(found)


def _scan_sheet(archive: zipfile.ZipFile, part: str) -> _Scan | None:
    """Look for formulas and merged cells in the raw XML of one sheet."""
    try:
        xml = archive.read(part)
    except KeyError:
        return None
    except (zipfile.BadZipFile, zlib.error, EOFError) as error:
        raise _Damaged(f"{type(error).__name__}: {error}") from error
    formulas: tuple[tuple[int, int, bool], ...] = ()
    if _FORMULA_TAG.search(xml) is not None:
        try:
            formulas = _formula_cells(xml)
        except (ET.ParseError, ValueError) as error:
            raise _Damaged(f"{type(error).__name__}: {error}") from error
    merges = tuple(
        (
            int(first_row),
            _column_number(first.decode()),
            int(last_row),
            _column_number(last.decode()),
        )
        for first, first_row, last, last_row in _MERGE_REF.findall(xml)
    )
    return _Scan(formulas=formulas, merges=merges)


# --------------------------------------------------------------------------- openpyxl guard


def _guarded(rows: Iterable[Any]) -> Iterator[Any]:
    """Iterate openpyxl rows; damage inside the sheet XML surfaces as ``_Damaged``.

    The guard wraps openpyxl's code only, so a bug in this module is never mistaken for
    a damaged file.
    """
    iterator = iter(rows)
    while True:
        try:
            row = next(iterator)
        except StopIteration:
            return
        except _DAMAGE_ERRORS as error:
            raise _Damaged(f"{type(error).__name__}: {error}") from error
        yield row


# --------------------------------------------------------------------------- Meta sheet


def _read_meta(worksheet: Any) -> WorkbookMeta | None:
    """Read the key/value rows of the Meta sheet. None when a required field is missing."""
    worksheet.reset_dimensions()
    pairs: dict[str, str] = {}
    for raw in _guarded(worksheet.iter_rows(values_only=True, max_col=2)):
        if len(raw) >= 2:
            key = cell_text(raw[0])
            if key:
                pairs.setdefault(key, cell_text(raw[1]))
    fields: dict[str, Any] = {}
    for field in dataclasses.fields(WorkbookMeta):
        kind = str(field.type)
        text = pairs.get(field.name, "")
        if not text:
            if "None" not in kind:
                return None
            fields[field.name] = None
        elif kind.startswith("int"):
            if not re.fullmatch(r"-?[0-9]+", text):
                return None
            fields[field.name] = int(text)
        else:
            fields[field.name] = text
    return WorkbookMeta(**fields)


def _short(commit: str | None) -> str:
    return commit[:12] if commit else "(not known)"


def _meta_findings(meta: WorkbookMeta | None, current: WorkbookMeta, file: str) -> list[Finding]:
    name = Path(file).name
    if meta is None:
        return [
            Finding(
                code=codes.META_MISSING,
                severity=Severity.WARNING,
                message=(
                    f"'{name}' has no complete '{Sheet.META.value}' sheet. PortTool cannot "
                    f"tell which state of the project it was exported from."
                ),
                file=file,
            )
        ]
    findings: list[Finding] = []
    if meta.baseline_commit != current.baseline_commit or meta.model_hash != current.model_hash:
        findings.append(
            Finding(
                code=codes.META_STALE_BASELINE,
                severity=Severity.WARNING,
                message=(
                    f"'{name}' was exported from another state of the project (commit "
                    f"{_short(meta.baseline_commit)}, exported {meta.export_time_utc}; the "
                    f"project is now at {_short(current.baseline_commit)}). Every request "
                    f"is checked against the current project."
                ),
                file=file,
            )
        )
    if meta.layout_version < current.layout_version:
        findings.append(
            Finding(
                code=codes.META_LAYOUT_OLDER,
                severity=Severity.WARNING,
                message=(
                    f"'{name}' uses workbook layout {meta.layout_version}; this PortTool "
                    f"writes layout {current.layout_version}. Columns are matched by "
                    f"their header. Export a new workbook for the next round."
                ),
                file=file,
            )
        )
    elif meta.layout_version > current.layout_version:
        findings.append(
            Finding(
                code=codes.META_LAYOUT_NEWER,
                severity=Severity.ERROR,
                message=(
                    f"'{name}' uses workbook layout {meta.layout_version}, written by a "
                    f"newer PortTool. This PortTool knows layout {current.layout_version}. "
                    f"Update PortTool before you import this file."
                ),
                file=file,
            )
        )
    return findings


# --------------------------------------------------------------------------- request sheets


def _map_columns(
    header: Sequence[object] | None, sheet: Sheet, file: str, findings: list[Finding]
) -> dict[str, int]:
    """Match the header row to the layout by text. Returns ``{column key: position}``."""
    columns = COLUMNS[sheet]
    expected: dict[str, Column] = {_header_key(column.header): column for column in columns}
    positions: dict[str, int] = {}
    where = RowRef(file=file, sheet=sheet.value, row=1)
    for position, raw in enumerate(header or ()):
        text = cell_text(raw)
        if not text:
            continue
        column = expected.get(_header_key(raw))
        letters = _column_letters(position + 1)
        if column is None:
            findings.append(
                Finding(
                    code=codes.COLUMN_UNKNOWN,
                    severity=Severity.WARNING,
                    message=(
                        f"Sheet '{sheet.value}', column {letters}: the header '{text}' is "
                        f"not part of the workbook layout. The column was ignored."
                    ),
                    file=file,
                    row=where,
                )
            )
        elif column.key in positions:
            first = _column_letters(positions[column.key] + 1)
            findings.append(
                Finding(
                    code=codes.COLUMN_DUPLICATE,
                    severity=Severity.WARNING,
                    message=(
                        f"Sheet '{sheet.value}' has the column '{column.header}' twice "
                        f"({first} and {letters}). Column {first} was read, column "
                        f"{letters} was ignored."
                    ),
                    file=file,
                    row=where,
                )
            )
        else:
            positions[column.key] = position
    blocking = KEY_ROW_ID not in positions or KEY_ACTION not in positions
    for column in columns:
        if column.key in positions:
            continue
        if column.key in OPTIONAL_KEYS:
            code, severity = codes.COLUMN_OPTIONAL_MISSING, Severity.WARNING
        else:
            code, severity = codes.COLUMN_MISSING, Severity.ERROR
        if blocking:
            effect = "The sheet was not imported."
        else:
            effect = "Its cells are read as empty."
        findings.append(
            Finding(
                code=code,
                severity=severity,
                message=f"Sheet '{sheet.value}': the column '{column.header}' is missing. {effect}",
                file=file,
                row=where,
            )
        )
    return {} if blocking else positions


def _row_finding(code: str, severity: Severity, where: RowRef, text: str) -> Finding:
    return Finding(
        code=code,
        severity=severity,
        message=f"Sheet '{where.sheet}', row {where.row}: {text}",
        file=where.file,
        row=where,
    )


def _read_rows(
    rows: Iterator[Any],
    sheet: Sheet,
    positions: Mapping[str, int],
    file: str,
    findings: list[Finding],
) -> list[RawRequestRow]:
    """Read the rows below the header. ``rows`` continues after the header row."""
    columns = COLUMNS[sheet]
    keyed = [(column.key, positions.get(column.key)) for column in columns]
    headers = {column.key: column.header for column in columns}
    data_keys = [key for key, position in keyed if position is not None and key not in REQUEST_KEYS]
    data_positions = [positions[key] for key in data_keys]
    action_at = positions[KEY_ACTION]
    row_id_at = positions[KEY_ROW_ID]
    width = max(positions.values()) + 1
    pattern = row_id_pattern(sheet)
    id_header = headers[KEY_ROW_ID]
    action_header = headers[KEY_ACTION]

    seen: dict[str, int] = {}
    existing: list[tuple[int, tuple[object, ...]]] = []
    result: list[RawRequestRow] = []
    number = 1
    for raw in rows:
        number += 1
        if not raw:
            continue
        if len(raw) < width:
            raw = (*raw, *((None,) * (width - len(raw))))
        raw_id = raw[row_id_at]
        if raw[action_at] is None:
            if raw_id is None:
                if raw.count(None) == len(raw):
                    continue  # blank row
            elif type(raw_id) is str and pattern.fullmatch(raw_id) is not None:
                # Untouched existing row: by far the most common case.
                first = seen.setdefault(raw_id, number)
                if first == number:
                    existing.append((number, raw))
                    continue
        values = {
            key: (cell_text(raw[position]) if position is not None else "")
            for key, position in keyed
        }
        where = RowRef(file=file, sheet=sheet.value, row=number)
        row_id_text = values[KEY_ROW_ID]
        action = values[KEY_ACTION].upper()
        if row_id_text:
            if pattern.fullmatch(row_id_text) is None:
                findings.append(
                    _row_finding(
                        codes.ROW_ID_FORMAT,
                        Severity.ERROR,
                        where,
                        f"'{row_id_text}' is not a {id_header} of this sheet. Do not type "
                        f"or change a {id_header}; leave it empty for a new row.",
                    )
                )
                well_formed = False
            else:
                well_formed = True
            first = seen.setdefault(row_id_text, number)
            if first != number:
                findings.append(
                    _row_finding(
                        codes.ROW_ID_DUPLICATE,
                        Severity.ERROR,
                        where,
                        f"the {id_header} '{row_id_text}' is already used in row {first}. "
                        f"A copied row must not keep the {id_header}.",
                    )
                )
            elif well_formed:
                existing.append((number, raw))
            if not action:
                continue
        elif not action:
            if not any(values.values()):
                continue  # white space only, or content in ignored columns only
            findings.append(
                _row_finding(
                    codes.ROW_ACTION_MISSING,
                    Severity.ERROR,
                    where,
                    f"the row has content but no {action_header}. Set {action_header} to "
                    f"{Action.ADD.value} for a new entry, or clear the row.",
                )
            )
        if action and action not in _ACTIONS:
            findings.append(
                _row_finding(
                    codes.ROW_ACTION_UNKNOWN,
                    Severity.ERROR,
                    where,
                    f"'{values[KEY_ACTION]}' is not an {action_header}. Use one of "
                    f"{', '.join(sorted(_ACTIONS))}.",
                )
            )
        for key, position in keyed:
            if position is not None and isinstance(raw[position], _DATE_TYPES):
                findings.append(
                    _row_finding(
                        codes.CELL_DATE,
                        Severity.ERROR,
                        where,
                        f"Excel turned the entry in column '{headers[key]}' into a date "
                        f"or time ({values[key]}). Format the cell as Text and type the "
                        f"value again.",
                    )
                )
        values[KEY_ACTION] = action
        result.append(
            RawRequestRow(
                sheet=sheet.value,
                row=where,
                row_id=row_id_text or None,
                action=action,
                requester=values[KEY_REQUESTER],
                comment=values[KEY_COMMENT],
                values=values,
            )
        )

    new_rows = [row for row in result if row.row_id is None]
    if new_rows and existing and data_keys:
        signatures: dict[tuple[str, ...], int] = {}
        for existing_number, existing_raw in existing:
            signature = tuple(
                [
                    value if type(value) is str else cell_text(value)
                    for value in [existing_raw[position] for position in data_positions]
                ]
            )
            signatures.setdefault(signature, existing_number)
        for row in new_rows:
            signature = tuple([row.values[key] for key in data_keys])
            twin = signatures.get(signature) if any(signature) else None
            if twin is not None:
                findings.append(
                    _row_finding(
                        codes.ROW_REPEATS_EXISTING,
                        Severity.INFO,
                        row.row,
                        f"this new row repeats the existing row {twin}.",
                    )
                )
    return result


def _scan_findings(
    scan: _Scan,
    sheet: Sheet,
    positions: Mapping[str, int],
    request_rows: frozenset[int],
    file: str,
) -> list[Finding]:
    """Findings for formulas and merged cells in the columns that were read."""
    columns = {position + 1 for position in positions.values()}
    findings: list[Finding] = []
    for row, column, has_value in scan.formulas:
        if row < 2 or column not in columns:
            continue
        where = RowRef(file=file, sheet=sheet.value, row=row)
        cell = f"{_column_letters(column)}{row}"
        if has_value:
            findings.append(
                _row_finding(
                    codes.CELL_FORMULA_CACHED,
                    Severity.WARNING,
                    where,
                    f"cell {cell} holds a formula. PortTool used the value Excel "
                    f"calculated last. Replace the formula by its value.",
                )
            )
        else:
            findings.append(
                _row_finding(
                    codes.CELL_FORMULA_NO_VALUE,
                    Severity.ERROR,
                    where,
                    f"cell {cell} holds a formula without a stored value, so the cell "
                    f"reads as empty. Replace the formula by its value.",
                )
            )
    for first_row, first_column, last_row, last_column in scan.merges:
        if not columns.intersection(range(first_column, last_column + 1)):
            continue
        hit = sorted(request_rows.intersection(range(first_row, last_row + 1)))
        where = RowRef(file=file, sheet=sheet.value, row=hit[0] if hit else first_row)
        area = (
            f"{_column_letters(first_column)}{first_row}:{_column_letters(last_column)}{last_row}"
        )
        findings.append(
            _row_finding(
                codes.CELL_MERGED,
                Severity.ERROR if hit else Severity.WARNING,
                where,
                f"the cells {area} are merged. Only the first cell keeps its value, the "
                f"others read as empty. Unmerge the cells and fill each one.",
            )
        )
    return findings


def _read_request_sheet(
    worksheet: Any, sheet: Sheet, file: str, scan: _Scan | None, findings: list[Finding]
) -> list[RawRequestRow]:
    worksheet.reset_dimensions()  # never trust the stored dimension: it may be too small
    rows = _guarded(worksheet.iter_rows(values_only=True))
    positions = _map_columns(next(rows, None), sheet, file, findings)
    if not positions:
        return []
    result = _read_rows(rows, sheet, positions, file, findings)
    if scan is None:
        findings.append(
            Finding(
                code=codes.SHEET_NOT_CHECKED,
                severity=Severity.WARNING,
                message=(
                    f"Sheet '{sheet.value}' could not be checked for formulas and merged "
                    f"cells. Its rows were imported."
                ),
                file=file,
            )
        )
    else:
        request_rows = frozenset(row.row.row for row in result)
        findings.extend(_scan_findings(scan, sheet, positions, request_rows, file))
    return result


# --------------------------------------------------------------------------- one workbook


def _renamed_from(worksheet: Any, missing: Sequence[Sheet]) -> Sheet | None:
    """The missing request sheet whose header row this unknown sheet carries, if any."""
    worksheet.reset_dimensions()
    header = next(_guarded(worksheet.iter_rows(values_only=True, max_row=1)), None)
    present = {_header_key(value) for value in header or ()}
    for sheet in sorted(missing, key=lambda s: len(COLUMNS[s]), reverse=True):
        needed = {
            _header_key(column.header)
            for column in COLUMNS[sheet]
            if column.key not in OPTIONAL_KEYS
        }
        if needed <= present:
            return sheet
    return None


def _read_workbook(
    workbook: Any, archive: zipfile.ZipFile, file: str, current: WorkbookMeta
) -> tuple[list[RawRequestRow], list[Finding], WorkbookMeta | None]:
    name = Path(file).name
    findings: list[Finding] = []
    known = {sheet.value.casefold(): sheet for sheet in Sheet}
    found: dict[Sheet, Any] = {}
    unknown: list[Any] = []
    for worksheet in workbook.worksheets:
        title = str(worksheet.title)
        sheet = known.get(clean_text(title).casefold())
        if sheet is None:
            unknown.append(worksheet)
        elif sheet in found:
            findings.append(
                Finding(
                    code=codes.SHEET_DUPLICATE,
                    severity=Severity.WARNING,
                    message=(
                        f"'{name}' has two sheets named '{sheet.value}' "
                        f"('{found[sheet].title}' and '{title}'). The first one was read."
                    ),
                    file=file,
                )
            )
        else:
            found[sheet] = worksheet

    meta = _read_meta(found[Sheet.META]) if Sheet.META in found else None
    findings.extend(_meta_findings(meta, current, file))

    missing = [sheet for sheet in REQUEST_SHEETS if sheet not in found]
    renamed: set[Sheet] = set()
    for worksheet in unknown:
        origin = _renamed_from(worksheet, [s for s in missing if s not in renamed])
        if origin is None:
            findings.append(
                Finding(
                    code=codes.SHEET_UNKNOWN,
                    severity=Severity.WARNING,
                    message=(
                        f"'{name}': the sheet '{worksheet.title}' is not part of the "
                        f"workbook layout and was ignored."
                    ),
                    file=file,
                )
            )
        else:
            renamed.add(origin)
            findings.append(
                Finding(
                    code=codes.SHEET_RENAMED,
                    severity=Severity.ERROR,
                    message=(
                        f"'{name}': the sheet '{worksheet.title}' looks like the sheet "
                        f"'{origin.value}' under another name. It was not imported. "
                        f"Rename it back to '{origin.value}'."
                    ),
                    file=file,
                )
            )
    for sheet in missing:
        if sheet not in renamed:
            findings.append(
                Finding(
                    code=codes.SHEET_MISSING,
                    severity=Severity.WARNING,
                    message=(f"'{name}' has no sheet '{sheet.value}'. No request was read for it."),
                    file=file,
                )
            )

    parts = _sheet_parts(archive)
    rows: list[RawRequestRow] = []
    for sheet in REQUEST_SHEETS:
        worksheet = found.get(sheet)
        if worksheet is None:
            continue
        part = parts.get(str(worksheet.title))
        scan = _scan_sheet(archive, part) if part is not None else None
        rows.extend(_read_request_sheet(worksheet, sheet, file, scan, findings))
    return rows, findings, meta


def _corrupt(file: str, detail: str) -> Finding:
    return Finding(
        code=codes.FILE_CORRUPT,
        severity=Severity.ERROR,
        message=(
            f"'{Path(file).name}' is damaged or is not an Excel workbook ({detail}). "
            f"Nothing was imported from it."
        ),
        file=file,
    )


def _encrypted(file: str) -> Finding:
    return Finding(
        code=codes.FILE_ENCRYPTED,
        severity=Severity.ERROR,
        message=(
            f"'{Path(file).name}' is password-protected, or it is an old binary .xls "
            f"file with an .xlsx name. Remove the password (File > Info > Protect "
            f"Workbook) or save it as Excel Workbook (.xlsx). Nothing was imported from it."
        ),
        file=file,
    )


def _import_bytes(
    data: bytes, file: str, current: WorkbookMeta
) -> tuple[list[RawRequestRow], list[Finding], WorkbookMeta | None] | Finding:
    """Read one workbook from memory. A Finding means: nothing usable in this file."""
    if data.startswith(_OLE_MAGIC):
        return _encrypted(file)
    if not data.startswith(_ZIP_MAGIC):
        return _corrupt(file, "it is empty" if not data else "it is not a zip package")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except _DAMAGE_ERRORS as error:
        return _corrupt(file, f"{type(error).__name__}: {error}")
    with archive:
        if any(info.flag_bits & _ZIP_ENCRYPTED for info in archive.infolist()):
            return _encrypted(file)
        try:
            workbook = openpyxl.load_workbook(
                io.BytesIO(data), read_only=True, data_only=True, keep_links=False
            )
        except _DAMAGE_ERRORS as error:
            return _corrupt(file, f"{type(error).__name__}: {error}")
        try:
            return _read_workbook(workbook, archive, file, current)
        except _Damaged as error:
            return _corrupt(file, str(error))
        finally:
            workbook.close()


# --------------------------------------------------------------------------- files


def _read_bytes(path: Path) -> bytes:
    """Read the whole file. Seam for tests; Excel can hold a file so that this fails."""
    return path.read_bytes()


def _load_file(
    path: Path, delays: Sequence[float], sleep: Callable[[float], None]
) -> bytes | Finding:
    """Read ``path``; wait and try again while another program locks it."""
    file = str(path)
    waits: list[float | None] = [*delays, None]
    for wait in waits:
        try:
            return _read_bytes(path)
        except FileNotFoundError:
            return Finding(
                code=codes.FILE_NOT_FOUND,
                severity=Severity.ERROR,
                message=f"The workbook '{file}' does not exist.",
                file=file,
            )
        except PermissionError as error:
            if path.is_dir():
                break
            if wait is None:
                return Finding(
                    code=codes.FILE_LOCKED,
                    severity=Severity.ERROR,
                    message=(
                        f"'{path.name}' cannot be read ({error.strerror or error}). It "
                        f"is probably open in Excel. Close it and import again."
                    ),
                    file=file,
                )
            sleep(wait)
        except IsADirectoryError:
            break
        except OSError as error:
            return Finding(
                code=codes.FILE_IO_ERROR,
                severity=Severity.ERROR,
                message=f"'{path.name}' could not be read: {error.strerror or error}.",
                file=file,
            )
    return Finding(
        code=codes.FILE_NOT_FOUND,
        severity=Severity.ERROR,
        message=f"'{file}' is a folder, not a workbook.",
        file=file,
    )


def import_workbooks(
    files: Sequence[Path],
    current: WorkbookMeta,
    retry_delays_s: Sequence[float] = (0.1, 0.2, 0.4),
    sleep: Callable[[float], None] = time.sleep,
) -> ImportedWorkbooks:
    """Read the request rows of ``files``. Blocking. See the module docstring."""
    rows: list[RawRequestRow] = []
    findings: list[Finding] = []
    metas: list[tuple[str, WorkbookMeta | None]] = []
    done: set[str] = set()
    for path in files:
        file = str(path)
        if path.name.startswith(_LOCK_FILE_PREFIX):
            findings.append(
                Finding(
                    code=codes.FILE_LOCK_FILE_SKIPPED,
                    severity=Severity.INFO,
                    message=(
                        f"'{path.name}' is an Excel lock file and was skipped. The "
                        f"workbook it belongs to is open in Excel: changes that are not "
                        f"saved yet are not imported."
                    ),
                    file=file,
                )
            )
            continue
        suffix = path.suffix.lower()
        if suffix != ".xlsx":
            what = _UNSUPPORTED.get(suffix, "not an Excel workbook (.xlsx)")
            findings.append(
                Finding(
                    code=codes.FILE_UNSUPPORTED_FORMAT,
                    severity=Severity.ERROR,
                    message=f"'{path.name}' is {what}. Nothing was imported from it.",
                    file=file,
                )
            )
            continue
        identity = os.path.normcase(os.path.abspath(path))
        if identity in done:
            findings.append(
                Finding(
                    code=codes.FILE_DUPLICATE,
                    severity=Severity.INFO,
                    message=f"'{path.name}' was given twice. It was imported once.",
                    file=file,
                )
            )
            continue
        done.add(identity)
        loaded = _load_file(path, retry_delays_s, sleep)
        if isinstance(loaded, Finding):
            findings.append(loaded)
            continue
        outcome = _import_bytes(loaded, file, current)
        if isinstance(outcome, Finding):
            findings.append(outcome)
            continue
        file_rows, file_findings, meta = outcome
        rows.extend(file_rows)
        findings.extend(file_findings)
        metas.append((file, meta))
    return ImportedWorkbooks(rows=tuple(rows), findings=tuple(findings), metas=tuple(metas))
