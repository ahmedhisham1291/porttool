"""What the workbook package adds to the layout contract in ``porttool.core.sheets``.

Sheet names, column keys and header texts live in ``porttool.core.sheets`` only. This
module holds the facts that contract does not state: the order of the sheets in the
file, the names of the two request-independent sheets' columns, and the name of the
defined name behind each dropdown list.
"""

from __future__ import annotations

import re
from typing import Final

from porttool.core.rowid import row_id
from porttool.core.sheets import COLUMNS, REQUEST_SHEETS, Sheet

#: Order of the sheets in an exported workbook.
SHEET_ORDER: Final[tuple[Sheet, ...]] = (
    Sheet.README,
    Sheet.COMPONENTS,
    Sheet.DATA_TYPES,
    Sheet.INTERFACES,
    Sheet.INTERFACE_ELEMENTS,
    Sheet.PORTS,
    Sheet.RUNNABLE_ACCESS,
    Sheet.RUNNABLE_TRIGGERS,
    Sheet.CONNECTIONS,
    Sheet.UNCONNECTED,
    Sheet.RESULTS,
    Sheet.LISTS,
    Sheet.META,
)

HIDDEN_SHEETS: Final[frozenset[Sheet]] = frozenset({Sheet.LISTS, Sheet.META})

#: Column keys every request sheet needs before a single row can be read.
KEY_ROW_ID: Final = "row_id"
KEY_ACTION: Final = "action"
KEY_REQUESTER: Final = "requester"
KEY_COMMENT: Final = "comment"
REQUEST_KEYS: Final[frozenset[str]] = frozenset(
    {KEY_ROW_ID, KEY_ACTION, KEY_REQUESTER, KEY_COMMENT}
)
#: Columns whose absence is reported but does not make a row unreadable.
OPTIONAL_KEYS: Final[frozenset[str]] = frozenset({KEY_REQUESTER, KEY_COMMENT})

#: Header texts of the Meta sheet (row 1). The rows below are WorkbookMeta fields.
META_HEADERS: Final[tuple[str, str]] = ("Key", "Value")

#: Excel limits.
EXCEL_MAX_ROWS: Final = 1_048_576
EXCEL_MAX_CELL_CHARS: Final = 32_767

_ROW_ID_DIGEST: Final = r"-[0-9a-f]{12}"


def list_range_name(list_name: str) -> str:
    """Defined name of the Lists-sheet range that feeds the dropdown ``list_name``.

    The prefix keeps every name a legal Excel name whatever the contract calls the list
    (a bare ``dt1`` would be read as a cell reference).
    """
    return f"pt_{list_name}"


def row_id_prefix(sheet: Sheet) -> str:
    """The two-letter RowID prefix of ``sheet``, taken from the RowID contract."""
    return row_id(sheet, "").partition("-")[0]


def row_id_pattern(sheet: Sheet) -> re.Pattern[str]:
    """Pattern a RowID on ``sheet`` must match completely."""
    return re.compile(re.escape(row_id_prefix(sheet)) + _ROW_ID_DIGEST)


def dropdown_names() -> frozenset[str]:
    """Every list name the layout contract refers to."""
    return frozenset(
        column.dropdown
        for columns in COLUMNS.values()
        for column in columns
        if column.dropdown is not None
    )


def is_request_sheet(sheet: Sheet) -> bool:
    return sheet in REQUEST_SHEETS
