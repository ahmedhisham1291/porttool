"""Import: which rows come back, how cell text is normalised, and every odd file."""

from __future__ import annotations

import datetime
import errno
import io as stdio
import shutil
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from porttool.core.models import Model
from porttool.core.results import RawRequestRow, RowRef, Severity
from porttool.core.rowid import row_id
from porttool.core.sheets import COLUMNS, REQUEST_SHEETS, Sheet, WorkbookMeta
from porttool.workbook import XlsxWorkbookIO, codes, importer
from tests.helpers.sample_model import CT
from tests.unit.workbook.conftest import SleepRecorder
from tests.unit.workbook.helpers import (
    codes_of,
    column_of,
    edit,
    header_of,
    layout_workbook,
    make_meta,
    patch_parts,
    set_cells,
    sheet_part,
)

PORTS = Sheet.PORTS
FIRST_BLANK = 8  # Ports sheet of the sample model: header, six ports, then the blank rows
PP_SPEED = row_id(Sheet.PORTS, f"{CT}/SwcA/PpSpeed")
RP_DIAG = row_id(Sheet.PORTS, f"{CT}/SwcA/RpDiag")

Change = Callable[[Any], None]


def new_port(worksheet: Any, row: int, name: Any = "RpNew", **more: Any) -> None:
    values: dict[str, Any] = {
        "swc": "SwcA",
        "port": name,
        "direction": "R",
        "interface": "IfSpeed",
        "action": "ADD",
    }
    values.update(more)
    set_cells(worksheet, PORTS, row, **values)


def edited(exported: Path, change: Change, name: str = "edited.xlsx") -> Path:
    return edit(exported, exported.with_name(name), change)


def only_row(rows: tuple[RawRequestRow, ...]) -> RawRequestRow:
    assert len(rows) == 1, rows
    return rows[0]


# --------------------------------------------------------------------------- plain cases


def test_untouched_export_yields_no_rows_and_no_findings(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta
) -> None:
    imported = io.import_rows([exported], meta)
    assert imported.rows == ()
    assert imported.findings == ()
    assert imported.metas == ((str(exported), meta),)


def test_action_on_an_existing_row_returns_that_row(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta
) -> None:
    def change(workbook: Any) -> None:
        set_cells(workbook["Ports"], PORTS, 3, action="DELETE", requester="Ada", comment="unused")

    path = edited(exported, change)
    imported = io.import_rows([path], meta)
    assert imported.findings == ()
    row = only_row(imported.rows)
    assert row == RawRequestRow(
        sheet="Ports",
        row=RowRef(file=str(path), sheet="Ports", row=3),
        row_id=RP_DIAG,
        action="DELETE",
        requester="Ada",
        comment="unused",
        values={
            "row_id": RP_DIAG,
            "swc": "SwcA",
            "port": "RpDiag",
            "direction": "R",
            "interface": "IfDiag",
            "interface_kind": "client-server",
            "connected": "Y",
            "partner_count": "1",
            "init_value": "",
            "action": "DELETE",
            "requester": "Ada",
            "comment": "unused",
        },
    )
    assert list(row.values) == [column.key for column in COLUMNS[PORTS]]


def test_existing_row_without_action_is_not_a_request(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta
) -> None:
    def change(workbook: Any) -> None:
        set_cells(workbook["Ports"], PORTS, 3, requester="Ada", comment="just a note")

    imported = io.import_rows([edited(exported, change)], meta)
    assert imported.rows == () and imported.findings == ()


def test_action_is_upper_cased(io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta) -> None:
    def change(workbook: Any) -> None:
        set_cells(workbook["Ports"], PORTS, 3, action=" delete ")
        new_port(workbook["Ports"], FIRST_BLANK, action="Add")

    imported = io.import_rows([edited(exported, change)], meta)
    assert [row.action for row in imported.rows] == ["DELETE", "ADD"]
    assert [row.values["action"] for row in imported.rows] == ["DELETE", "ADD"]
    assert imported.findings == ()


def test_action_outside_the_list_returns_finding_and_the_row(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta
) -> None:
    def change(workbook: Any) -> None:
        set_cells(workbook["Ports"], PORTS, 3, action="remove")

    path = edited(exported, change)
    imported = io.import_rows([path], meta)
    assert only_row(imported.rows).action == "REMOVE"
    assert codes_of(imported.findings) == [codes.ROW_ACTION_UNKNOWN]
    finding = imported.findings[0]
    assert finding.severity is Severity.ERROR
    assert finding.row == RowRef(file=str(path), sheet="Ports", row=3)
    assert "'remove' is not an Action. Use one of ADD, DELETE, MODIFY." in finding.message


def test_new_row_without_action_returns_finding_and_the_row(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta
) -> None:
    def change(workbook: Any) -> None:
        new_port(workbook["Ports"], FIRST_BLANK, action=None)

    path = edited(exported, change)
    imported = io.import_rows([path], meta)
    row = only_row(imported.rows)
    assert (row.row_id, row.action, row.values["port"]) == (None, "", "RpNew")
    assert codes_of(imported.findings) == [codes.ROW_ACTION_MISSING]
    assert imported.findings[0].row == row.row
    assert imported.findings[0].severity is Severity.ERROR


def test_same_row_id_twice_returns_finding(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta
) -> None:
    def change(workbook: Any) -> None:
        worksheet = workbook["Ports"]
        # A copied row keeps its RowID: once with an Action, once without.
        new_port(worksheet, FIRST_BLANK, name="PpSpeed2", row_id=PP_SPEED)
        set_cells(worksheet, PORTS, FIRST_BLANK + 1, row_id=RP_DIAG, port="RpDiag")

    path = edited(exported, change)
    imported = io.import_rows([path], meta)
    row = only_row(imported.rows)
    assert (row.row_id, row.row.row) == (PP_SPEED, FIRST_BLANK)
    assert codes_of(imported.findings) == [codes.ROW_ID_DUPLICATE, codes.ROW_ID_DUPLICATE]
    first, second = imported.findings
    assert first.row == RowRef(file=str(path), sheet="Ports", row=FIRST_BLANK)
    assert f"'{PP_SPEED}' is already used in row 2" in first.message
    assert second.row == RowRef(file=str(path), sheet="Ports", row=FIRST_BLANK + 1)
    assert f"'{RP_DIAG}' is already used in row 3" in second.message


def test_same_row_id_in_two_workbooks_is_not_a_duplicate(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta
) -> None:
    def change(workbook: Any) -> None:
        set_cells(workbook["Ports"], PORTS, 2, action="DELETE")

    team_a = edited(exported, change, "team_a.xlsx")
    team_b = edited(exported, change, "team_b.xlsx")
    imported = io.import_rows([team_a, team_b], meta)
    assert [row.row_id for row in imported.rows] == [PP_SPEED, PP_SPEED]
    assert imported.findings == ()


def test_row_id_in_the_wrong_format_returns_finding(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta
) -> None:
    connector_id = row_id(Sheet.CONNECTIONS, "/some/connector")

    def change(workbook: Any) -> None:
        worksheet = workbook["Ports"]
        new_port(worksheet, FIRST_BLANK, row_id="PT-123", action="MODIFY")
        set_cells(worksheet, PORTS, FIRST_BLANK + 1, row_id=connector_id, action="DELETE")
        set_cells(worksheet, PORTS, FIRST_BLANK + 2, row_id="my own id")  # no Action
        set_cells(worksheet, PORTS, FIRST_BLANK + 3, row_id=PP_SPEED.upper(), action="DELETE")
        set_cells(worksheet, PORTS, FIRST_BLANK + 4, row_id=4711, action="DELETE")

    path = edited(exported, change)
    imported = io.import_rows([path], meta)
    assert [row.row_id for row in imported.rows] == [
        "PT-123",
        connector_id,
        PP_SPEED.upper(),
        "4711",
    ]
    assert codes_of(imported.findings) == [codes.ROW_ID_FORMAT] * 5
    assert [finding.row.row for finding in imported.findings if finding.row] == [8, 9, 10, 11, 12]
    assert "'my own id' is not a RowID of this sheet" in imported.findings[2].message


def test_new_row_identical_to_an_existing_object_is_returned_with_a_note(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta
) -> None:
    def change(workbook: Any) -> None:
        worksheet = workbook["Ports"]
        # Row 2 copied without its RowID: SwcA.PpSpeed again, typed the way a user would.
        new_port(
            worksheet,
            FIRST_BLANK,
            name="PpSpeed",
            direction="P",
            interface_kind="sender-receiver",
            connected="Y",
            partner_count="1",
            init_value="Speed=0",
        )
        new_port(worksheet, FIRST_BLANK + 1, name="RpOther")

    path = edited(exported, change)
    imported = io.import_rows([path], meta)
    assert [(row.row_id, row.action, row.values["port"]) for row in imported.rows] == [
        (None, "ADD", "PpSpeed"),
        (None, "ADD", "RpOther"),
    ]
    assert codes_of(imported.findings) == [codes.ROW_REPEATS_EXISTING]
    finding = imported.findings[0]
    assert finding.severity is Severity.INFO
    assert finding.row == RowRef(file=str(path), sheet="Ports", row=FIRST_BLANK)
    assert "repeats the existing row 2" in finding.message


# --------------------------------------------------------------------------- cell text


def test_leading_and_trailing_spaces_are_stripped(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta
) -> None:
    def change(workbook: Any) -> None:
        new_port(
            workbook["Ports"],
            FIRST_BLANK,
            name="  RpNew \t",
            swc=" SwcA",
            interface="IfSpeed\n",
            requester="  Ada Lovelace  ",
            comment=" two  inner  spaces stay ",
        )

    row = only_row(io.import_rows([edited(exported, change)], meta).rows)
    assert (row.values["swc"], row.values["port"], row.values["interface"]) == (
        "SwcA",
        "RpNew",
        "IfSpeed",
    )
    assert (row.requester, row.comment) == ("Ada Lovelace", "two  inner  spaces stay")


def test_non_breaking_spaces_are_normalised(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta
) -> None:
    def change(workbook: Any) -> None:
        new_port(
            workbook["Ports"],
            FIRST_BLANK,
            name=" RpNew ",
            action=" add ",
            comment="pasted from a web page",
        )
        # A row that holds nothing but non-breaking spaces is an empty row.
        set_cells(workbook["Ports"], PORTS, FIRST_BLANK + 1, port=" ", comment="   ")

    imported = io.import_rows([edited(exported, change)], meta)
    row = only_row(imported.rows)
    assert (row.values["port"], row.action, row.comment) == (
        "RpNew",
        "ADD",
        "pasted from a web page",
    )
    assert imported.findings == ()


def test_numbers_stored_as_text_and_text_stored_as_numbers(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta
) -> None:
    def change(workbook: Any) -> None:
        worksheet = workbook["RunnableTriggers"]
        base = {"swc": "SwcA", "runnable": "RunA_10ms", "trigger_kind": "periodic", "action": "ADD"}
        periods: list[Any] = [
            20,
            20.0,
            "20",
            " 20 ",
            2.5,
            "2.50",
            0.1 + 0.2,
            1e-05,
            12345678901234567890,
        ]
        for offset, period in enumerate(periods):
            set_cells(worksheet, Sheet.RUNNABLE_TRIGGERS, 5 + offset, period_ms=period, **base)
        set_cells(worksheet, Sheet.RUNNABLE_TRIGGERS, 20, task=True, port=False, **base)
        set_cells(worksheet, Sheet.RUNNABLE_TRIGGERS, 21, runnable=4711, swc="SwcA", action="ADD")

    imported = io.import_rows([edited(exported, change)], meta)
    assert [row.values["period_ms"] for row in imported.rows[:9]] == [
        "20",  # integer
        "20",  # float 20.0: no trailing ".0"
        "20",  # text
        "20",  # text with spaces
        "2.5",
        "2.50",  # text stays as typed
        "0.3",  # 0.30000000000000004 shown the way Excel shows it
        "1e-05",
        "1.23456789012346e+19",
    ]
    booleans = imported.rows[9]
    assert (booleans.values["task"], booleans.values["port"]) == ("TRUE", "FALSE")
    assert imported.rows[10].values["runnable"] == "4711"
    assert imported.findings == ()


def test_value_auto_converted_to_a_date_returns_finding(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta
) -> None:
    def change(workbook: Any) -> None:
        worksheet = workbook["Ports"]
        # What Excel does to "MAR1", "1-2" or "12:30" typed into a General cell.
        new_port(worksheet, FIRST_BLANK, name=datetime.datetime(2026, 3, 1))
        new_port(worksheet, FIRST_BLANK + 1, init_value=datetime.time(12, 30))
        new_port(worksheet, FIRST_BLANK + 2, init_value=datetime.timedelta(hours=30))
        # A date in a row that is not a request is nobody's problem.
        worksheet.cell(row=600, column=30, value=datetime.date(2026, 1, 1))

    path = edited(exported, change)
    imported = io.import_rows([path], meta)
    assert len(imported.rows) == 3
    assert codes_of(imported.findings) == [codes.CELL_DATE] * 3
    first = imported.findings[0]
    assert first.severity is Severity.ERROR
    assert first.row == RowRef(file=str(path), sheet="Ports", row=FIRST_BLANK)
    assert "column 'Port' into a date or time (2026-03-01T00:00:00)" in first.message
    assert "Format the cell as Text" in first.message
    assert imported.rows[0].values["port"] == "2026-03-01T00:00:00"
    assert imported.rows[1].values["init_value"] == "12:30:00"
    assert imported.rows[2].values["init_value"] == "1 day, 6:00:00"
    assert "column 'Init value'" in imported.findings[2].message


def cache_formula_results(path: Path, target: Path, results: dict[str, tuple[str, str]]) -> Path:
    """Give formula cells a stored value, the way Excel saves them.

    ``results`` maps a cell reference to (cell type, value): openpyxl itself writes
    formulas without a stored value.
    """

    def patch(xml: bytes) -> bytes:
        text = xml.decode("utf-8")
        for cell, (kind, value) in results.items():
            start = text.index(f'<c r="{cell}"')
            end = text.index("</c>", start)
            body = text[start:end]
            head, _, rest = body.partition(">")
            assert rest.endswith("<v></v>") or rest.endswith("<v />"), rest
            rest = rest.rsplit("<v", 1)[0] + f"<v>{value}</v>"
            text = text[:start] + f'{head} t="{kind}">' + rest + text[end:]
        return text.encode("utf-8")

    return patch_parts(path, target, {sheet_part(path, "Ports"): patch})


def test_formula_without_cached_value_returns_finding_and_reads_as_empty(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta
) -> None:
    def change(workbook: Any) -> None:
        worksheet = workbook["Ports"]
        new_port(worksheet, FIRST_BLANK, name='="Rp"&"New"')
        # A formula in the Action cell hides the whole request: it must not vanish silently.
        new_port(worksheet, FIRST_BLANK + 2, action='=IF(1=1,"ADD","")')
        worksheet.cell(row=FIRST_BLANK + 4, column=40, value="=1+1")  # outside the layout

    path = edited(exported, change)
    imported = io.import_rows([path], meta)
    assert [row.row.row for row in imported.rows] == [FIRST_BLANK, FIRST_BLANK + 2]
    assert imported.rows[0].values["port"] == ""
    assert imported.rows[1].action == ""
    assert codes_of(imported.findings) == [
        codes.ROW_ACTION_MISSING,
        codes.CELL_FORMULA_NO_VALUE,
        codes.CELL_FORMULA_NO_VALUE,
    ]
    port_formula = imported.findings[1]
    assert port_formula.severity is Severity.ERROR
    assert port_formula.row == RowRef(file=str(path), sheet="Ports", row=FIRST_BLANK)
    assert "cell C8 holds a formula without a stored value" in port_formula.message
    assert "cell J10" in imported.findings[2].message


def test_formula_with_cached_value_is_used_and_reported(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta
) -> None:
    def change(workbook: Any) -> None:
        new_port(workbook["Ports"], FIRST_BLANK, name='="Rp"&"New"', init_value="=1+1")

    without_values = edited(exported, change)
    path = cache_formula_results(
        without_values,
        exported.with_name("calculated.xlsx"),
        {"C8": ("str", "RpNew"), "I8": ("n", "2")},
    )
    imported = io.import_rows([path], meta)
    row = only_row(imported.rows)
    assert (row.values["port"], row.values["init_value"]) == ("RpNew", "2")
    assert codes_of(imported.findings) == [codes.CELL_FORMULA_CACHED, codes.CELL_FORMULA_CACHED]
    assert {finding.severity for finding in imported.findings} == {Severity.WARNING}
    assert "cell C8 holds a formula. PortTool used the value Excel calculated last" in (
        imported.findings[0].message
    )
    assert imported.findings[0].row == row.row


def test_merged_cells_are_reported(io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta) -> None:
    def change(workbook: Any) -> None:
        worksheet = workbook["Ports"]
        new_port(worksheet, FIRST_BLANK)
        new_port(worksheet, FIRST_BLANK + 1, swc=None)
        worksheet.merge_cells(
            start_row=FIRST_BLANK, start_column=2, end_row=FIRST_BLANK + 1, end_column=2
        )
        worksheet.merge_cells("K20:L21")  # in blank rows: no request is affected
        worksheet.merge_cells("Z30:AA31")  # outside the layout columns: ignored

    path = edited(exported, change)
    imported = io.import_rows([path], meta)
    # Only the first cell of a merged range keeps its value: row 9 has lost its SWC.
    assert [row.values["swc"] for row in imported.rows] == ["SwcA", ""]
    assert codes_of(imported.findings) == [codes.CELL_MERGED, codes.CELL_MERGED]
    hit, harmless = imported.findings
    assert hit.severity is Severity.ERROR
    assert hit.row == RowRef(file=str(path), sheet="Ports", row=FIRST_BLANK)
    assert "the cells B8:B9 are merged" in hit.message
    assert harmless.severity is Severity.WARNING
    assert "K20:L21" in harmless.message


def test_hidden_rows_and_filtered_rows_are_still_imported(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta
) -> None:
    def change(workbook: Any) -> None:
        worksheet = workbook["Ports"]
        set_cells(worksheet, PORTS, 3, action="DELETE")
        new_port(worksheet, FIRST_BLANK)
        # Hidden by hand.
        worksheet.row_dimensions[FIRST_BLANK].hidden = True
        # Filtered out: Excel stores a filter as criteria plus hidden rows.
        worksheet.auto_filter.add_filter_column(1, ["SwcB"])
        for row in (2, 3, 4, 7):
            worksheet.row_dimensions[row].hidden = True

    path = edited(exported, change)
    with zipfile.ZipFile(path) as archive:
        xml = archive.read(sheet_part(path, "Ports")).decode("utf-8")
    assert '<row r="3" hidden="1">' in xml and "<filterColumn" in xml
    imported = io.import_rows([path], meta)
    assert [(row.row.row, row.action) for row in imported.rows] == [(3, "DELETE"), (8, "ADD")]
    assert imported.findings == ()


def test_empty_rows_between_data_are_skipped_and_row_numbers_stay_right(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta
) -> None:
    def change(workbook: Any) -> None:
        worksheet = workbook["Ports"]
        new_port(worksheet, FIRST_BLANK + 3, name="RpAfterGap")
        new_port(worksheet, 300, name="RpFarDown")
        new_port(worksheet, 2000, name="RpBelowTheBlankRows")

    imported = io.import_rows([edited(exported, change)], meta)
    assert [(row.row.row, row.values["port"]) for row in imported.rows] == [
        (11, "RpAfterGap"),
        (300, "RpFarDown"),
        (2000, "RpBelowTheBlankRows"),
    ]
    assert imported.findings == ()


def test_content_in_ignored_columns_only_is_not_a_request(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta
) -> None:
    def change(workbook: Any) -> None:
        worksheet = workbook["Ports"]
        worksheet.cell(row=1, column=14, value="My notes")
        worksheet.cell(row=FIRST_BLANK, column=14, value="remember to ask Bob")
        worksheet.cell(row=FIRST_BLANK + 1, column=20, value="stray text without a header")

    imported = io.import_rows([edited(exported, change)], meta)
    assert imported.rows == ()
    assert codes_of(imported.findings) == [codes.COLUMN_UNKNOWN]


# --------------------------------------------------------------------------- columns


def test_reordered_columns_are_matched_by_header(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta
) -> None:
    def change(workbook: Any) -> None:
        worksheet = workbook["Ports"]
        set_cells(worksheet, PORTS, 3, action="DELETE", requester="Ada")
        new_port(worksheet, FIRST_BLANK)
        rows = [
            list(row)
            for row in worksheet.iter_rows(min_row=1, max_row=FIRST_BLANK, values_only=True)
        ]
        for row_number, values in enumerate(rows, start=1):
            for column_number, value in enumerate(reversed(values), start=1):
                worksheet.cell(row=row_number, column=column_number).value = value

    path = edited(exported, change)
    imported = io.import_rows([path], meta)
    assert imported.findings == ()
    delete, add = imported.rows
    assert (delete.row_id, delete.action, delete.requester) == (RP_DIAG, "DELETE", "Ada")
    assert (delete.values["swc"], delete.values["port"], delete.values["partner_count"]) == (
        "SwcA",
        "RpDiag",
        "1",
    )
    assert (add.row_id, add.values["port"], add.values["interface"]) == (None, "RpNew", "IfSpeed")
    assert list(add.values) == [column.key for column in COLUMNS[PORTS]]


def test_headers_with_extra_spaces_or_other_case_are_matched(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta
) -> None:
    def change(workbook: Any) -> None:
        worksheet = workbook["Ports"]
        worksheet.cell(row=1, column=column_of(PORTS, "row_id"), value="  rowid ")
        worksheet.cell(row=1, column=column_of(PORTS, "port"), value="PORT")
        worksheet.cell(row=1, column=column_of(PORTS, "interface_kind"), value="interface   KIND")
        worksheet.cell(row=1, column=column_of(PORTS, "connected"), value="Connected (y/n) ")
        worksheet.cell(row=1, column=column_of(PORTS, "action"), value="action\n")
        new_port(worksheet, FIRST_BLANK, interface_kind="sender-receiver")
        set_cells(worksheet, PORTS, 2, action="DELETE")

    imported = io.import_rows([edited(exported, change)], meta)
    assert imported.findings == ()
    delete, add = imported.rows
    assert (delete.row_id, delete.values["connected"]) == (PP_SPEED, "Y")
    assert (add.values["port"], add.values["interface_kind"]) == ("RpNew", "sender-receiver")


def test_added_column_is_ignored_with_finding(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta
) -> None:
    def change(workbook: Any) -> None:
        worksheet = workbook["Ports"]
        worksheet.insert_cols(3)
        worksheet.cell(row=1, column=3, value="Priority")
        worksheet.cell(row=2, column=3, value="high")
        set_cells(worksheet, PORTS, 2, comment=None)
        worksheet.cell(row=2, column=column_of(PORTS, "action") + 1, value="DELETE")

    path = edited(exported, change)
    imported = io.import_rows([path], meta)
    row = only_row(imported.rows)
    assert (row.row_id, row.action, row.values["port"]) == (PP_SPEED, "DELETE", "PpSpeed")
    assert "Priority" not in row.values and "high" not in row.values.values()
    assert codes_of(imported.findings) == [codes.COLUMN_UNKNOWN]
    finding = imported.findings[0]
    assert finding.severity is Severity.WARNING
    assert "column C: the header 'Priority' is not part of the workbook layout" in finding.message
    assert finding.row == RowRef(file=str(path), sheet="Ports", row=1)


def test_deleted_column_returns_finding_and_its_cells_read_as_empty(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta
) -> None:
    def change(workbook: Any) -> None:
        worksheet = workbook["Ports"]
        set_cells(worksheet, PORTS, 2, action="DELETE", comment="bye")
        worksheet.delete_cols(column_of(PORTS, "comment"))
        worksheet.delete_cols(column_of(PORTS, "interface"))

    imported = io.import_rows([edited(exported, change)], meta)
    row = only_row(imported.rows)
    assert (row.row_id, row.action, row.comment) == (PP_SPEED, "DELETE", "")
    assert (row.values["interface"], row.values["interface_kind"]) == ("", "sender-receiver")
    assert list(row.values) == [column.key for column in COLUMNS[PORTS]]
    assert codes_of(imported.findings) == [codes.COLUMN_MISSING, codes.COLUMN_OPTIONAL_MISSING]
    required, optional = imported.findings
    assert required.severity is Severity.ERROR
    assert "the column 'Interface' is missing. Its cells are read as empty." in required.message
    assert optional.severity is Severity.WARNING
    assert "the column 'Comment' is missing" in optional.message


@pytest.mark.parametrize("key", ["row_id", "action"])
def test_sheet_without_row_id_or_action_column_is_not_imported(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta, key: str
) -> None:
    def change(workbook: Any) -> None:
        worksheet = workbook["Ports"]
        set_cells(worksheet, PORTS, 2, action="DELETE")
        worksheet.cell(row=1, column=column_of(PORTS, key)).value = None
        set_cells(workbook["Connections"], Sheet.CONNECTIONS, 2, action="DELETE")

    imported = io.import_rows([edited(exported, change)], meta)
    assert [row.sheet for row in imported.rows] == ["Connections"]
    assert codes_of(imported.findings) == [codes.COLUMN_MISSING]
    assert imported.findings[0].message == (
        f"Sheet 'Ports': the column '{header_of(PORTS, key)}' is missing. "
        f"The sheet was not imported."
    )


def test_duplicate_column_header_reads_the_first_one(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta
) -> None:
    def change(workbook: Any) -> None:
        worksheet = workbook["Ports"]
        worksheet.cell(row=1, column=13, value="Action")
        worksheet.cell(row=2, column=13, value="ADD")
        set_cells(worksheet, PORTS, 2, action="DELETE")

    imported = io.import_rows([edited(exported, change)], meta)
    assert only_row(imported.rows).action == "DELETE"
    assert codes_of(imported.findings) == [codes.COLUMN_DUPLICATE]
    assert "the column 'Action' twice (J and M)" in imported.findings[0].message


def test_sheet_without_any_header_reports_every_column(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta
) -> None:
    def change(workbook: Any) -> None:
        worksheet = workbook["Interfaces"]
        worksheet.delete_rows(1, worksheet.max_row)

    imported = io.import_rows([edited(exported, change)], meta)
    assert imported.rows == ()
    assert codes_of(imported.findings) == [
        codes.COLUMN_MISSING,  # RowID
        codes.COLUMN_MISSING,  # Interface
        codes.COLUMN_MISSING,  # Kind
        codes.COLUMN_MISSING,  # Action
        codes.COLUMN_OPTIONAL_MISSING,
        codes.COLUMN_OPTIONAL_MISSING,
    ]


# --------------------------------------------------------------------------- sheets


def test_missing_sheet_returns_finding_and_the_other_sheets_are_imported(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta
) -> None:
    def change(workbook: Any) -> None:
        set_cells(workbook["Ports"], PORTS, 2, action="DELETE")
        del workbook["Connections"]
        del workbook["README"]  # not a request sheet: nobody misses it
        del workbook["Unconnected"]

    path = edited(exported, change)
    imported = io.import_rows([path], meta)
    assert only_row(imported.rows).row_id == PP_SPEED
    assert codes_of(imported.findings) == [codes.SHEET_MISSING]
    finding = imported.findings[0]
    assert finding.severity is Severity.WARNING
    assert (
        finding.message == "'edited.xlsx' has no sheet 'Connections'. No request was read for it."
    )
    assert finding.file == str(path)


def test_renamed_sheet_is_reported_and_not_imported(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta
) -> None:
    def change(workbook: Any) -> None:
        set_cells(workbook["Ports"], PORTS, 2, action="DELETE")
        workbook["Ports"].title = "Ports Team A"
        set_cells(workbook["Interfaces"], Sheet.INTERFACES, 2, action="DELETE")

    imported = io.import_rows([edited(exported, change)], meta)
    assert [row.sheet for row in imported.rows] == ["Interfaces"]
    assert codes_of(imported.findings) == [codes.SHEET_RENAMED]
    finding = imported.findings[0]
    assert finding.severity is Severity.ERROR
    assert "the sheet 'Ports Team A' looks like the sheet 'Ports' under another name" in (
        finding.message
    )
    assert "Rename it back to 'Ports'" in finding.message


def test_unknown_extra_sheet_is_ignored_with_finding(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta
) -> None:
    def change(workbook: Any) -> None:
        notes = workbook.create_sheet("Team notes")
        notes["A1"] = "RowID"
        notes["A2"] = "something"
        workbook.create_sheet("Empty")

    imported = io.import_rows([edited(exported, change)], meta)
    assert imported.rows == ()
    assert codes_of(imported.findings) == [codes.SHEET_UNKNOWN, codes.SHEET_UNKNOWN]
    assert {finding.severity for finding in imported.findings} == {Severity.WARNING}
    assert "the sheet 'Team notes' is not part of the workbook layout" in (
        imported.findings[0].message
    )


def test_sheet_name_in_other_case_or_with_spaces_is_still_found(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta
) -> None:
    def change(workbook: Any) -> None:
        set_cells(workbook["Ports"], PORTS, 2, action="DELETE")
        workbook["Ports"].title = " PORTS "
        workbook["Meta"].title = "META\u00a0"

    imported = io.import_rows([edited(exported, change)], meta)
    row = only_row(imported.rows)
    assert (row.sheet, row.row.sheet, row.row_id) == ("Ports", "Ports", PP_SPEED)
    assert imported.findings == ()
    assert imported.metas[0][1] == meta


def test_two_sheets_with_the_same_name_read_the_first(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta
) -> None:
    def change(workbook: Any) -> None:
        set_cells(workbook["Ports"], PORTS, 2, action="DELETE")
        copy = workbook.copy_worksheet(workbook["Ports"])
        copy.title = "ports "
        set_cells(copy, PORTS, 3, action="DELETE")

    imported = io.import_rows([edited(exported, change)], meta)
    assert [row.row.row for row in imported.rows] == [2]
    assert codes_of(imported.findings) == [codes.SHEET_DUPLICATE]
    assert "two sheets named 'Ports' ('Ports' and 'ports ')" in imported.findings[0].message


# --------------------------------------------------------------------------- Meta sheet


def test_stale_baseline_returns_finding(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta, model: Model
) -> None:
    moved_on = make_meta(model, baseline_commit="f" * 40)
    imported = io.import_rows([exported], moved_on)
    assert codes_of(imported.findings) == [codes.META_STALE_BASELINE]
    finding = imported.findings[0]
    assert finding.severity is Severity.WARNING
    assert finding.file == str(exported)
    assert "commit 0123456789ab" in finding.message and "now at ffffffffffff" in finding.message
    assert imported.metas == ((str(exported), meta),)

    other_files = make_meta(model, model_hash="0" * 64)
    assert codes_of(io.import_rows([exported], other_files).findings) == [codes.META_STALE_BASELINE]
    no_git = make_meta(model, baseline_commit=None)
    stale = io.import_rows([exported], no_git).findings
    assert codes_of(stale) == [codes.META_STALE_BASELINE]
    assert "now at (not known)" in stale[0].message
    # Tool version and export time are not part of the baseline.
    same = make_meta(model, tool_version="9.9.9", export_time_utc="2030-01-01T00:00:00Z")
    assert io.import_rows([exported], same).findings == ()


def test_workbook_with_an_older_layout_version_returns_finding_and_is_imported(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta, model: Model
) -> None:
    def change(workbook: Any) -> None:
        set_cells(workbook["Ports"], PORTS, 2, action="DELETE")

    path = edited(exported, change)
    newer_tool = make_meta(model, layout_version=meta.layout_version + 1)
    imported = io.import_rows([path], newer_tool)
    assert only_row(imported.rows).row_id == PP_SPEED
    assert codes_of(imported.findings) == [codes.META_LAYOUT_OLDER]
    finding = imported.findings[0]
    assert finding.severity is Severity.WARNING
    assert "uses workbook layout 1; this PortTool writes layout 2" in finding.message


def test_workbook_with_a_newer_layout_version_returns_an_error_finding(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta, model: Model
) -> None:
    older_tool = make_meta(model, layout_version=meta.layout_version - 1)
    imported = io.import_rows([exported], older_tool)
    assert codes_of(imported.findings) == [codes.META_LAYOUT_NEWER]
    assert imported.findings[0].severity is Severity.ERROR
    assert "Update PortTool" in imported.findings[0].message


def test_missing_or_incomplete_meta_sheet_returns_finding(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta
) -> None:
    def no_sheet(workbook: Any) -> None:
        del workbook["Meta"]

    def no_hash(workbook: Any) -> None:
        for row in workbook["Meta"].iter_rows(min_row=2):
            if row[0].value == "model_hash":
                row[1].value = None

    def bad_version(workbook: Any) -> None:
        for row in workbook["Meta"].iter_rows(min_row=2):
            if row[0].value == "layout_version":
                row[1].value = "one"

    for name, change in (("a.xlsx", no_sheet), ("b.xlsx", no_hash), ("c.xlsx", bad_version)):
        path = edited(exported, change, name)
        imported = io.import_rows([path], meta)
        assert codes_of(imported.findings) == [codes.META_MISSING], name
        assert imported.findings[0].severity is Severity.WARNING
        assert imported.metas == ((str(path), None),)


def test_meta_values_stored_as_numbers_and_optional_fields_left_empty_are_read(
    io: XlsxWorkbookIO, exported: Path, model: Model
) -> None:
    def change(workbook: Any) -> None:
        for row in workbook["Meta"].iter_rows(min_row=2):
            if row[0].value == "layout_version":
                row[1].value = 1.0
            if row[0].value in ("baseline_commit", "autosar_schema"):
                row[1].value = None

    path = edited(exported, change)
    expected = make_meta(model, baseline_commit=None, autosar_schema=None)
    imported = io.import_rows([path], expected)
    assert imported.metas == ((str(path), expected),)
    assert imported.findings == ()


# --------------------------------------------------------------------------- other writers


def test_workbook_written_by_openpyxl_itself_is_imported(
    io: XlsxWorkbookIO, tmp_path: Path, meta: WorkbookMeta
) -> None:
    """Stands in for LibreOffice or Google Sheets: no defined names, other styles, no Meta."""
    workbook = layout_workbook(
        {
            Sheet.PORTS: [
                {"row_id": PP_SPEED, "swc": "SwcA", "port": "PpSpeed", "direction": "P"},
                {"row_id": RP_DIAG, "swc": "SwcA", "port": "RpDiag", "action": "delete"},
                {},
                {
                    "swc": "SwcB",
                    "port": "RpNew",
                    "direction": "R",
                    "interface": "IfSpeed",
                    "action": "ADD",
                },
            ],
            Sheet.CONNECTIONS: [
                {
                    "provider_swc": "SwcM",
                    "provider_port": "PpTorque",
                    "requester_swc": "SwcA",
                    "requester_port": "RpTorque",
                    "composition": "EcuComposition",
                    "action": "ADD",
                    "requester": "Grace",
                }
            ],
        }
    )
    path = tmp_path / "from_other_tool.xlsx"
    workbook.save(path)
    with zipfile.ZipFile(path) as archive:
        assert "<definedName " not in archive.read("xl/workbook.xml").decode("utf-8")

    imported = io.import_rows([path], meta)
    assert [(row.sheet, row.row.row, row.row_id, row.action) for row in imported.rows] == [
        ("Ports", 3, RP_DIAG, "DELETE"),
        ("Ports", 5, None, "ADD"),
        ("Connections", 2, None, "ADD"),
    ]
    connection = imported.rows[2]
    assert connection.requester == "Grace"
    assert connection.values["provider_swc"] == "SwcM"
    assert connection.values["connector_name"] == ""
    assert codes_of(imported.findings) == [
        codes.META_MISSING,
        codes.SHEET_MISSING,  # DataTypes
        codes.SHEET_MISSING,  # Interfaces
        codes.SHEET_MISSING,  # InterfaceElements
        codes.SHEET_MISSING,  # RunnableAccess
        codes.SHEET_MISSING,  # RunnableTriggers
    ]
    assert imported.metas == ((str(path), None),)


def test_stored_sheet_dimension_is_not_trusted(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta
) -> None:
    """Some writers store a dimension that is too small; rows beyond it must not vanish."""

    def change(workbook: Any) -> None:
        set_cells(workbook["Ports"], PORTS, 3, action="DELETE")
        new_port(workbook["Ports"], FIRST_BLANK)

    path = edited(exported, change)
    part = sheet_part(path, "Ports")

    def shrink(xml: bytes) -> bytes:
        assert b'<dimension ref="A1:L507"/>' in xml
        return xml.replace(b'<dimension ref="A1:L507"/>', b'<dimension ref="A1"/>')

    wrong = patch_parts(path, exported.with_name("wrong_dimension.xlsx"), {part: shrink})
    imported = io.import_rows([wrong], meta)
    assert [(row.row.row, row.action) for row in imported.rows] == [(3, "DELETE"), (8, "ADD")]
    assert imported.findings == ()


def test_sheet_that_cannot_be_checked_for_formulas_is_reported(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta, monkeypatch: pytest.MonkeyPatch
) -> None:
    def change(workbook: Any) -> None:
        set_cells(workbook["Ports"], PORTS, 2, action="DELETE")

    path = edited(exported, change)
    monkeypatch.setattr(importer, "_sheet_parts", lambda archive: {"Ports": "xl/nowhere.xml"})
    imported = io.import_rows([path], meta)
    assert only_row(imported.rows).row_id == PP_SPEED
    assert codes_of(imported.findings) == [codes.SHEET_NOT_CHECKED] * len(REQUEST_SHEETS)
    assert {finding.severity for finding in imported.findings} == {Severity.WARNING}


# --------------------------------------------------------------------------- files


def test_excel_lock_file_in_the_list_is_skipped(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta
) -> None:
    lock_file = exported.with_name("~$requests.xlsx")
    lock_file.write_bytes(b"\x05Ada" + b"\x20" * 50)  # what Excel writes: the user's name
    imported = io.import_rows([lock_file, exported], meta)
    assert codes_of(imported.findings) == [codes.FILE_LOCK_FILE_SKIPPED]
    finding = imported.findings[0]
    assert finding.severity is Severity.INFO
    assert finding.file == str(lock_file)
    assert "is an Excel lock file and was skipped" in finding.message
    assert imported.metas == ((str(exported), meta),)


@pytest.mark.parametrize(
    ("name", "hint"),
    [
        ("requests.xls", "old binary Excel file (.xls)"),
        ("requests.xlsm", "macro-enabled workbook (.xlsm)"),
        ("requests.csv", "a CSV file"),
        ("REQUESTS.XLS", "old binary Excel file (.xls)"),
        ("requests.ods", "not an Excel workbook (.xlsx)"),
        ("requests", "not an Excel workbook (.xlsx)"),
    ],
)
def test_xls_xlsm_and_csv_are_rejected_with_a_clear_finding(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta, name: str, hint: str
) -> None:
    other = Path(shutil.copy(exported, exported.with_name(name)))  # even with valid content
    imported = io.import_rows([other], meta)
    assert imported.rows == () and imported.metas == ()
    assert codes_of(imported.findings) == [codes.FILE_UNSUPPORTED_FORMAT]
    finding = imported.findings[0]
    assert finding.severity is Severity.ERROR
    assert hint in finding.message and name in finding.message
    assert finding.file == str(other)


def test_upper_case_xlsx_extension_is_accepted(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta
) -> None:
    upper = Path(shutil.copy(exported, exported.with_name("REQUESTS.XLSX")))
    assert io.import_rows([upper], meta).findings == ()


def test_missing_file_and_folder_return_findings(
    io: XlsxWorkbookIO, tmp_path: Path, meta: WorkbookMeta
) -> None:
    folder = tmp_path / "folder.xlsx"
    folder.mkdir()
    imported = io.import_rows([tmp_path / "nope.xlsx", folder], meta)
    assert codes_of(imported.findings) == [codes.FILE_NOT_FOUND, codes.FILE_NOT_FOUND]
    assert "does not exist" in imported.findings[0].message
    assert "is a folder, not a workbook" in imported.findings[1].message
    assert imported.metas == ()


def test_same_file_given_twice_is_imported_once(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta
) -> None:
    def change(workbook: Any) -> None:
        set_cells(workbook["Ports"], PORTS, 2, action="DELETE")

    path = edited(exported, change)
    again = path.parent / "." / path.name
    imported = io.import_rows([path, again], meta)
    assert len(imported.rows) == 1 and len(imported.metas) == 1
    assert codes_of(imported.findings) == [codes.FILE_DUPLICATE]
    assert imported.findings[0].severity is Severity.INFO


def test_workbook_open_in_excel_on_read_returns_finding_after_retries(
    io: XlsxWorkbookIO,
    sleeper: SleepRecorder,
    exported: Path,
    meta: WorkbookMeta,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[Path] = []

    def locked(path: Path) -> bytes:
        attempts.append(path)
        raise PermissionError(errno.EACCES, "Permission denied", str(path))

    monkeypatch.setattr(importer, "_read_bytes", locked)
    imported = io.import_rows([exported], meta)
    assert imported.rows == () and imported.metas == ()
    assert codes_of(imported.findings) == [codes.FILE_LOCKED]
    finding = imported.findings[0]
    assert finding.severity is Severity.ERROR
    assert "probably open in Excel. Close it and import again" in finding.message
    assert finding.file == str(exported)
    assert len(attempts) == 4
    assert sleeper.waits == [0.1, 0.2, 0.4]


def test_locked_workbook_is_read_once_it_is_released(
    io: XlsxWorkbookIO,
    sleeper: SleepRecorder,
    exported: Path,
    meta: WorkbookMeta,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def locked_once(path: Path) -> bytes:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError(errno.EACCES, "Permission denied", str(path))
        return path.read_bytes()

    monkeypatch.setattr(importer, "_read_bytes", locked_once)
    imported = io.import_rows([exported], meta)
    assert imported.findings == () and imported.metas == ((str(exported), meta),)
    assert sleeper.waits == [0.1]


def test_other_operating_system_error_on_read_returns_finding(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(path: Path) -> bytes:
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(importer, "_read_bytes", broken)
    imported = io.import_rows([exported], meta)
    assert codes_of(imported.findings) == [codes.FILE_IO_ERROR]
    assert "Input/output error" in imported.findings[0].message


def test_password_protected_or_encrypted_file_returns_finding(
    io: XlsxWorkbookIO, tmp_path: Path, meta: WorkbookMeta
) -> None:
    """Excel stores an encrypted workbook as an OLE compound file, not as a zip.

    No tool to encrypt a real workbook is at hand here: the file below only starts
    with the signature of such a container.
    """
    path = tmp_path / "secret.xlsx"
    path.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"\x00" * 2040)
    imported = io.import_rows([path], meta)
    assert imported.rows == () and imported.metas == ()
    assert codes_of(imported.findings) == [codes.FILE_ENCRYPTED]
    finding = imported.findings[0]
    assert finding.severity is Severity.ERROR
    assert "is password-protected" in finding.message and "Remove the password" in finding.message


def test_zip_with_encrypted_entries_returns_finding(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta
) -> None:
    data = bytearray(exported.read_bytes())
    central = data.index(b"PK\x01\x02")
    data[central + 8] |= 0x01  # general-purpose flag "encrypted" of the first entry
    path = exported.with_name("zip_encrypted.xlsx")
    path.write_bytes(bytes(data))
    assert codes_of(io.import_rows([path], meta).findings) == [codes.FILE_ENCRYPTED]


def _truncate(data: bytes) -> bytes:
    return data[: len(data) // 2]


def _corrupt_cases(exported: Path) -> dict[str, bytes]:
    data = exported.read_bytes()
    ports = sheet_part(exported, "Ports")
    folder = exported.parent
    not_a_workbook = stdio.BytesIO()
    with zipfile.ZipFile(not_a_workbook, "w") as archive:
        archive.writestr("readme.txt", "a zip, but not a workbook")
    cut_sheet = patch_parts(exported, folder / "p1.zip", {ports: _truncate})
    cut_strings = patch_parts(exported, folder / "p2.zip", {"xl/sharedStrings.xml": _truncate})
    bad_workbook = patch_parts(exported, folder / "p3.zip", {"xl/workbook.xml": lambda _: b"<x"})
    bad_number = patch_parts(
        exported, folder / "p4.zip", {ports: lambda xml: xml.replace(b"<v>1</v>", b"<v>x</v>", 1)}
    )
    info = zipfile.ZipFile(exported).getinfo(ports)
    broken_stream = bytearray(data)
    start = info.header_offset + 30 + len(info.filename) + 40
    broken_stream[start : start + 20] = b"\xff" * 20
    return {
        "truncated file": data[: len(data) // 2],
        "garbage with a zip signature": b"PK\x03\x04" + b"\x00garbage" * 64,
        "empty file": b"",
        "plain text": b"RowID;SWC;Port\n",
        "zip without a workbook": not_a_workbook.getvalue(),
        "sheet XML cut off": cut_sheet.read_bytes(),
        "shared strings cut off": cut_strings.read_bytes(),
        "workbook XML damaged": bad_workbook.read_bytes(),
        "text where a number belongs": bad_number.read_bytes(),
        "damaged compressed stream": bytes(broken_stream),
    }


def test_corrupt_zip_returns_finding_and_nothing_is_imported(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta
) -> None:
    cases = _corrupt_cases(exported)
    assert len(cases) == 10
    for label, data in cases.items():
        path = exported.with_name("damaged.xlsx")
        path.write_bytes(data)
        imported = io.import_rows([path], meta)
        assert codes_of(imported.findings) == [codes.FILE_CORRUPT], label
        finding = imported.findings[0]
        assert finding.severity is Severity.ERROR, label
        assert "is damaged or is not an Excel workbook" in finding.message, label
        assert "Nothing was imported from it" in finding.message, label
        assert imported.rows == () and imported.metas == (), label


def test_rows_read_before_the_damage_showed_are_not_returned(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta
) -> None:
    def change(workbook: Any) -> None:
        set_cells(workbook["DataTypes"], Sheet.DATA_TYPES, 2, action="DELETE")

    good = edited(exported, change, "good.xlsx")
    assert len(io.import_rows([good], meta).rows) == 1
    # DataTypes is read before Ports: its request row must not survive the damaged file.
    damaged = patch_parts(
        good, exported.with_name("cut.xlsx"), {sheet_part(good, "Ports"): _truncate}
    )
    imported = io.import_rows([damaged], meta)
    assert codes_of(imported.findings) == [codes.FILE_CORRUPT]
    assert imported.rows == () and imported.metas == ()


def test_damaged_formula_sheet_is_a_corrupt_file() -> None:
    with pytest.raises(importer._Damaged):
        archive = stdio.BytesIO()
        with zipfile.ZipFile(archive, "w") as package:
            package.writestr("xl/worksheets/sheet1.xml", b"<worksheet><sheetData><row><c><f>1")
        importer._scan_sheet(zipfile.ZipFile(archive), "xl/worksheets/sheet1.xml")


def test_several_workbooks_are_tagged_with_file_sheet_and_row(
    io: XlsxWorkbookIO, exported: Path, meta: WorkbookMeta
) -> None:
    def team_a(workbook: Any) -> None:
        set_cells(workbook["Ports"], PORTS, 2, action="DELETE", requester="A")
        new_port(workbook["Ports"], FIRST_BLANK, name="RpFromA")

    def team_b(workbook: Any) -> None:
        set_cells(workbook["Interfaces"], Sheet.INTERFACES, 3, action="MODIFY", requester="B")
        new_port(workbook["Ports"], FIRST_BLANK + 4, name="RpFromB")

    first = edited(exported, team_a, "team_a.xlsx")
    second = edited(exported, team_b, "team_b.xlsx")
    damaged = exported.with_name("damaged.xlsx")
    damaged.write_bytes(b"PK\x03\x04 not really")
    imported = io.import_rows([first, damaged, second], meta)
    assert [(row.row.file, row.row.sheet, row.row.row, row.requester) for row in imported.rows] == [
        (str(first), "Ports", 2, "A"),
        (str(first), "Ports", 8, ""),
        (str(second), "Interfaces", 3, "B"),
        (str(second), "Ports", 12, ""),
    ]
    assert all(row.sheet == row.row.sheet for row in imported.rows)
    # A damaged file does not stop the others.
    assert codes_of(imported.findings) == [codes.FILE_CORRUPT]
    assert imported.findings[0].file == str(damaged)
    assert imported.metas == ((str(first), meta), (str(second), meta))


def test_no_files_is_an_empty_import(io: XlsxWorkbookIO, meta: WorkbookMeta) -> None:
    imported = io.import_rows([], meta)
    assert (imported.rows, imported.findings, imported.metas) == ((), (), ())


# --------------------------------------------------------------------------- helpers


def test_cell_text() -> None:
    assert importer.cell_text(None) == ""
    assert importer.cell_text("  a b  ") == "a b"
    assert importer.cell_text(True) == "TRUE" and importer.cell_text(False) == "FALSE"
    assert importer.cell_text(7) == "7"
    assert importer.cell_text(7.0) == "7"
    assert importer.cell_text(-0.5) == "-0.5"
    assert importer.cell_text(1e15) == "1e+15"
    assert importer.cell_text(float("inf")) == "inf"
    assert importer.cell_text(datetime.date(2026, 3, 1)) == "2026-03-01"
    assert importer.cell_text(b"bytes ") == "b'bytes '"
    assert importer.number_text(123456789012345.0) == "123456789012345"


def test_column_letters_and_numbers() -> None:
    for number, letters in (
        (1, "A"),
        (26, "Z"),
        (27, "AA"),
        (52, "AZ"),
        (703, "AAA"),
        (16384, "XFD"),
    ):
        assert importer._column_letters(number) == letters
        assert importer._column_number(letters) == number


def test_formula_scan_copes_with_cells_without_reference_and_with_prefixes() -> None:
    xml = (
        b'<x:worksheet xmlns:x="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        b"<x:sheetData>"
        b"<x:row><x:c><x:v>1</x:v></x:c><x:c><x:f>A1+1</x:f><x:v>2</x:v></x:c></x:row>"
        b'<x:row r="5"><x:c r="C5" t="inlineStr"><x:f>A1</x:f><x:is><x:t>x</x:t></x:is></x:c>'
        b"<x:c><x:f>A1</x:f></x:c></x:row>"
        b"</x:sheetData>"
        b'<x:mergeCells count="1"><x:mergeCell ref="B7:C9"/></x:mergeCells>'
        b"</x:worksheet>"
    )
    assert importer._FORMULA_TAG.search(xml) is not None
    assert importer._formula_cells(xml) == ((1, 2, True), (5, 3, True), (5, 4, False))
    archive = stdio.BytesIO()
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("xl/worksheets/sheet1.xml", xml)
    scan = importer._scan_sheet(zipfile.ZipFile(archive), "xl/worksheets/sheet1.xml")
    assert scan is not None
    assert scan.merges == ((7, 2, 9, 3),)
    assert len(scan.formulas) == 3
    # Tags that merely start with "f" are not formulas.
    plain = b"<worksheet><sheetData/><filterColumn/><formula1>pt_swcs</formula1></worksheet>"
    assert importer._FORMULA_TAG.search(plain) is None


def test_sheet_parts_resolves_relative_and_absolute_targets() -> None:
    workbook_xml = (
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Ports" sheetId="1" r:id="rId1"/>'
        '<sheet name="Meta" sheetId="2" r:id="rId2"/>'
        '<sheet name="Lost" sheetId="3" r:id="rId9"/></sheets></workbook>'
    )
    relations_xml = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Target="/xl/worksheets/sheet2.xml"/></Relationships>'
    )
    archive = stdio.BytesIO()
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("xl/workbook.xml", workbook_xml)
        package.writestr("xl/_rels/workbook.xml.rels", relations_xml)
    assert importer._sheet_parts(zipfile.ZipFile(archive)) == {
        "Ports": "xl/worksheets/sheet1.xml",
        "Meta": "xl/worksheets/sheet2.xml",
    }
    empty = stdio.BytesIO()
    with zipfile.ZipFile(empty, "w") as package:
        package.writestr("other.txt", "x")
    assert importer._sheet_parts(zipfile.ZipFile(empty)) == {}
