"""Stable finding codes of the workbook package. Every code starts with ``WBK-``.

A code keeps its meaning for ever. Add new codes; never reuse or renumber one.
"""

from __future__ import annotations

from typing import Final

# ---- export

EXPORT_FOLDER_MISSING: Final = "WBK-EXPORT-001"  # target folder missing or not a folder
EXPORT_FOLDER_READ_ONLY: Final = "WBK-EXPORT-002"  # no temporary file can be created there
EXPORT_TARGET_LOCKED: Final = "WBK-EXPORT-003"  # target open in Excel or read-only
EXPORT_DISK_FULL: Final = "WBK-EXPORT-004"
EXPORT_IO_ERROR: Final = "WBK-EXPORT-005"  # any other operating-system error while saving
EXPORT_TOO_MANY_ROWS: Final = "WBK-EXPORT-006"  # a sheet exceeds Excel's row limit
EXPORT_BLANK_ROWS_REDUCED: Final = "WBK-EXPORT-007"
EXPORT_CELL_TRUNCATED: Final = "WBK-EXPORT-008"  # text above Excel's cell limit was cut
EXPORT_BAD_EXTENSION: Final = "WBK-EXPORT-009"  # target is not *.xlsx
EXPORT_TEMP_LEFT_BEHIND: Final = "WBK-EXPORT-010"  # the temporary file could not be removed

# ---- import: files

FILE_NOT_FOUND: Final = "WBK-FILE-001"
FILE_LOCK_FILE_SKIPPED: Final = "WBK-FILE-002"  # ~$name.xlsx
FILE_UNSUPPORTED_FORMAT: Final = "WBK-FILE-003"  # .xls, .xlsm, .csv, anything not .xlsx
FILE_LOCKED: Final = "WBK-FILE-004"  # PermissionError while reading
FILE_ENCRYPTED: Final = "WBK-FILE-005"  # password-protected, or a binary .xls behind .xlsx
FILE_CORRUPT: Final = "WBK-FILE-006"  # not a zip, damaged zip, damaged content
FILE_DUPLICATE: Final = "WBK-FILE-007"  # the same file was given twice
FILE_IO_ERROR: Final = "WBK-FILE-008"  # any other operating-system error while reading

# ---- import: sheets

SHEET_MISSING: Final = "WBK-SHEET-001"
SHEET_UNKNOWN: Final = "WBK-SHEET-002"  # not part of the layout, ignored
SHEET_RENAMED: Final = "WBK-SHEET-003"  # unknown sheet whose header matches a missing sheet
SHEET_DUPLICATE: Final = "WBK-SHEET-004"  # two sheets with the same name apart from case/spaces
SHEET_NOT_CHECKED: Final = "WBK-SHEET-005"  # formulas and merged cells could not be checked

# ---- import: columns

COLUMN_MISSING: Final = "WBK-COLUMN-001"  # required column missing
COLUMN_UNKNOWN: Final = "WBK-COLUMN-002"  # ignored
COLUMN_DUPLICATE: Final = "WBK-COLUMN-003"  # the first one wins
COLUMN_OPTIONAL_MISSING: Final = "WBK-COLUMN-004"  # Requester or Comment missing

# ---- import: rows and cells

ROW_ACTION_MISSING: Final = "WBK-ROW-001"  # new row (no RowID) with content but no Action
ROW_ACTION_UNKNOWN: Final = "WBK-ROW-002"  # Action outside ADD / MODIFY / DELETE
ROW_ID_FORMAT: Final = "WBK-ROW-003"
ROW_ID_DUPLICATE: Final = "WBK-ROW-004"
ROW_REPEATS_EXISTING: Final = "WBK-ROW-005"  # new row equal to an existing row of the sheet
CELL_DATE: Final = "WBK-CELL-001"  # Excel turned the entry into a date
CELL_FORMULA_CACHED: Final = "WBK-CELL-002"  # formula, its last calculated value was used
CELL_FORMULA_NO_VALUE: Final = "WBK-CELL-003"  # formula without a stored value: cell is empty
CELL_MERGED: Final = "WBK-CELL-004"

# ---- import: Meta sheet

META_MISSING: Final = "WBK-META-001"  # Meta sheet missing or incomplete
META_STALE_BASELINE: Final = "WBK-META-002"  # other baseline commit or model hash
META_LAYOUT_OLDER: Final = "WBK-META-003"
META_LAYOUT_NEWER: Final = "WBK-META-004"

ALL_CODES: Final[tuple[str, ...]] = tuple(
    value
    for name, value in sorted(globals().items())
    if name.isupper() and name != "ALL_CODES" and isinstance(value, str)
)
