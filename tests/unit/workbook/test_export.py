"""Export: structure of the written workbook, protection, dropdowns, determinism, saving."""

from __future__ import annotations

import errno
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import IO, Any

import openpyxl
import pytest
from xlsxwriter.exceptions import XlsxWriterException

from porttool.core.config import ProjectConfig
from porttool.core.errors import WorkbookError
from porttool.core.interfaces import WorkbookIO
from porttool.core.models import (
    Component,
    ComponentKind,
    CompuMethod,
    DataType,
    DataTypeCategory,
    DataTypeLevel,
    Model,
    Port,
    PortDirection,
)
from porttool.core.results import Finding, RequestResult, RequestStatus, RowRef, Severity
from porttool.core.sheets import COLUMNS, REQUEST_SHEETS, Sheet, WorkbookMeta
from porttool.workbook import ExportOptions, XlsxWorkbookIO, codes, exporter
from porttool.workbook.layout import SHEET_ORDER, dropdown_names, list_range_name
from porttool.workbook.lists import ENUM_LISTS
from tests.helpers.sample_model import CT, DT
from tests.unit.workbook.conftest import SleepRecorder
from tests.unit.workbook.helpers import (
    EXPORT_TIME,
    codes_of,
    column_of,
    data_row_count,
    make_meta,
    sheet_part,
)

DATA_ROWS = {
    Sheet.COMPONENTS: 4,
    Sheet.DATA_TYPES: 1,
    Sheet.INTERFACES: 3,
    Sheet.INTERFACE_ELEMENTS: 3,
    Sheet.PORTS: 6,
    Sheet.RUNNABLE_ACCESS: 3,
    Sheet.RUNNABLE_TRIGGERS: 3,
    Sheet.CONNECTIONS: 2,
    Sheet.UNCONNECTED: 2,
    Sheet.RESULTS: 0,
}


def load(path: Path) -> Any:
    return openpyxl.load_workbook(path)


def sheet_xml(path: Path, sheet: Sheet) -> str:
    with zipfile.ZipFile(path) as archive:
        return archive.read(sheet_part(path, sheet.value)).decode("utf-8")


def temp_files(folder: Path) -> list[str]:
    return sorted(entry.name for entry in folder.iterdir() if entry.suffix == ".tmp")


# --------------------------------------------------------------------------- structure


def test_xlsx_workbook_io_satisfies_the_workbook_io_contract() -> None:
    workbook_io: WorkbookIO = XlsxWorkbookIO()
    assert isinstance(workbook_io, XlsxWorkbookIO)
    assert XlsxWorkbookIO().options == ExportOptions()
    assert ExportOptions().blank_rows == 500


def test_sheets_come_in_the_contract_order_with_lists_and_meta_hidden(exported: Path) -> None:
    workbook = load(exported)
    assert workbook.sheetnames == [sheet.value for sheet in SHEET_ORDER]
    states = {worksheet.title: worksheet.sheet_state for worksheet in workbook.worksheets}
    assert [title for title, state in states.items() if state == "hidden"] == ["Lists", "Meta"]
    assert workbook.active.title == "README"


def test_header_row_holds_the_layout_headers(exported: Path) -> None:
    workbook = load(exported)
    for sheet, columns in COLUMNS.items():
        header = [cell.value for cell in workbook[sheet.value][1]]
        assert header == [column.header for column in columns], sheet


def test_one_row_per_object_then_500_blank_rows_on_request_sheets(exported: Path) -> None:
    workbook = load(exported)
    for sheet, expected in DATA_ROWS.items():
        worksheet = workbook[sheet.value]
        assert data_row_count(worksheet) == expected, sheet
        blank = 500 if sheet in REQUEST_SHEETS else 0
        assert worksheet.max_row == 1 + expected + blank, sheet
        assert worksheet.max_column == len(COLUMNS[sheet]), sheet


def test_number_of_blank_rows_is_configurable(
    tmp_path: Path, model: Model, config: ProjectConfig, meta: WorkbookMeta
) -> None:
    target = tmp_path / "few.xlsx"
    assert XlsxWorkbookIO(ExportOptions(blank_rows=3)).export(model, config, target, meta) == ()
    workbook = load(target)
    assert workbook["Ports"].max_row == 1 + 6 + 3
    assert workbook["Ports"].auto_filter.ref == "A1:L10"
    none = tmp_path / "none.xlsx"
    assert XlsxWorkbookIO(ExportOptions(blank_rows=0)).export(model, config, none, meta) == ()
    assert load(none)["Ports"].max_row == 1 + 6


def test_cells_are_locked_except_request_columns_and_blank_rows(exported: Path) -> None:
    workbook = load(exported)
    for sheet in REQUEST_SHEETS:
        worksheet = workbook[sheet.value]
        rows = DATA_ROWS[sheet]
        columns = COLUMNS[sheet]
        for index, column in enumerate(columns, start=1):
            assert worksheet.cell(1, index).protection.locked, (sheet, "header")
            for row in range(2, 2 + rows):
                locked = worksheet.cell(row, index).protection.locked
                assert locked is (not column.editable), (sheet, row, column.key)
            for row in (2 + rows, 1 + rows + 500):
                assert not worksheet.cell(row, index).protection.locked, (sheet, row, column.key)
            # The first row after the blank rows is locked again.
            assert worksheet.cell(2 + rows + 500, index).protection.locked
    for sheet in (Sheet.COMPONENTS, Sheet.UNCONNECTED, Sheet.RESULTS):
        worksheet = workbook[sheet.value]
        for row in worksheet.iter_rows(min_row=1, max_row=worksheet.max_row + 1):
            assert all(cell.protection.locked for cell in row), sheet


def test_editable_cells_are_formatted_as_text(exported: Path) -> None:
    worksheet = load(exported)["Ports"]
    action = column_of(Sheet.PORTS, "action")
    assert worksheet.cell(2, action).number_format == "@"
    assert worksheet.cell(20, 1).number_format == "@"  # a blank row
    assert worksheet.cell(2, column_of(Sheet.PORTS, "port")).number_format == "@"
    assert worksheet.cell(2, column_of(Sheet.PORTS, "partner_count")).number_format == "General"


def test_every_sheet_is_protected_without_a_password(exported: Path) -> None:
    workbook = load(exported)
    for worksheet in workbook.worksheets:
        assert worksheet.protection.sheet is True, worksheet.title
        assert not worksheet.protection.password, worksheet.title
    with zipfile.ZipFile(exported) as archive:
        for name in archive.namelist():
            if name.startswith("xl/worksheets/"):
                xml = archive.read(name).decode("utf-8")
                protection = re.search(r"<sheetProtection[^>]*>", xml)
                assert protection is not None, name
                assert "password" not in protection.group(0)
                assert "algorithmName" not in protection.group(0)
    ports = workbook["Ports"].protection
    # openpyxl reports "protected" flags: False means the user may still do it.
    assert ports.autoFilter is False
    assert ports.formatColumns is False
    assert ports.insertRows is True
    assert ports.deleteRows is True
    assert ports.sort is True


def test_header_row_is_frozen_and_autofilter_spans_data_and_blank_rows(exported: Path) -> None:
    workbook = load(exported)
    for sheet, rows in DATA_ROWS.items():
        worksheet = workbook[sheet.value]
        assert worksheet.freeze_panes == "A2", sheet
        last_column = openpyxl.utils.get_column_letter(len(COLUMNS[sheet]))
        last_row = 1 + rows + (500 if sheet in REQUEST_SHEETS else 0)
        assert worksheet.auto_filter.ref == f"A1:{last_column}{last_row}", sheet


def test_columns_are_wide_enough_for_header_and_content(exported: Path) -> None:
    worksheet = load(exported)["Connections"]
    name = openpyxl.utils.get_column_letter(column_of(Sheet.CONNECTIONS, "connector_name"))
    assert worksheet.column_dimensions[name].width >= len("SwcA_PpSpeed_SwcB_RpSpeed")
    comment = openpyxl.utils.get_column_letter(column_of(Sheet.CONNECTIONS, "comment"))
    assert 8 <= worksheet.column_dimensions[comment].width <= 60


def test_dropdowns_reference_named_ranges_on_the_lists_sheet(exported: Path) -> None:
    workbook = load(exported)
    lists = workbook["Lists"]
    list_columns = {cell.value: cell.column_letter for cell in lists[1]}
    assert set(list_columns) >= dropdown_names()
    for name in dropdown_names():
        defined = workbook.defined_names[list_range_name(name)]
        letter = list_columns[name]
        match = re.fullmatch(rf"Lists!\${letter}\$2:\${letter}\$(\d+)", defined.attr_text)
        assert match is not None, defined.attr_text
        entries = [cell.value for cell in lists[letter][1:] if cell.value is not None]
        assert int(match.group(1)) == max(len(entries), 1) + 1, name

    for sheet in REQUEST_SHEETS:
        worksheet = workbook[sheet.value]
        validations = {str(v.sqref): v for v in worksheet.data_validations.dataValidation}
        expected = {}
        last_row = 1 + DATA_ROWS[sheet] + 500
        for index, column in enumerate(COLUMNS[sheet], start=1):
            if column.dropdown is not None:
                letter = openpyxl.utils.get_column_letter(index)
                expected[f"{letter}2:{letter}{last_row}"] = column.dropdown
        assert set(validations) == set(expected), sheet
        for area, list_name in expected.items():
            validation = validations[area]
            assert validation.type == "list"
            # A reference to a defined name, never an inline "a,b,c" list (255-character limit).
            assert validation.formula1 == list_range_name(list_name)
            style = validation.errorStyle or "stop"
            assert style == ("stop" if list_name in ENUM_LISTS else "information"), list_name
    for sheet in (Sheet.COMPONENTS, Sheet.UNCONNECTED, Sheet.RESULTS, Sheet.README):
        assert not workbook[sheet.value].data_validations.dataValidation


def test_lists_sheet_holds_the_model_names_and_the_enum_values(exported: Path) -> None:
    lists = load(exported)["Lists"]
    content = {
        column[0].value: [cell.value for cell in column[1:] if cell.value is not None]
        for column in lists.iter_cols()
    }
    assert content["swcs"] == ["EcuComposition", "SwcA", "SwcB", "SwcM"]
    assert content["actions"] == ["ADD", "MODIFY", "DELETE"]
    assert content["tasks"] == ["Task_10ms", "Task_Event", "Task_Init"]
    assert content["units"] == []


def test_numbers_are_written_as_numbers_and_text_as_text(exported: Path) -> None:
    workbook = load(exported)
    ports = workbook["Ports"]
    partners = ports.cell(2, column_of(Sheet.PORTS, "partner_count"))
    assert (partners.value, partners.data_type) == (1, "n")
    triggers = workbook["RunnableTriggers"]
    period = triggers.cell(2, column_of(Sheet.RUNNABLE_TRIGGERS, "period_ms"))
    assert (period.value, period.data_type) == (10, "n")
    init = ports.cell(2, column_of(Sheet.PORTS, "init_value"))
    assert (init.value, init.data_type) == ("Speed=0", "s")


def test_readme_states_rules_legend_baseline_and_export_time(
    exported: Path, meta: WorkbookMeta
) -> None:
    worksheet = load(exported)["README"]
    left = [row[0] for row in worksheet.iter_rows(values_only=True)]
    lines = [" ".join(str(v) for v in row if v) for row in worksheet.iter_rows(values_only=True)]
    text = "\n".join(lines)
    assert left[0] == "PortTool request workbook"
    assert f"Baseline commit {meta.baseline_commit}" in lines
    assert f"Exported (UTC) {EXPORT_TIME}" in lines
    assert f"Model hash {meta.model_hash}" in lines
    for heading in ("Baseline", "How to enter a request", "Rules", "Cell formats", "Legend"):
        assert heading in left, heading
    assert "set Action to ADD" in text
    assert "Leave RowID empty" in text
    assert "500 per sheet" in text
    assert "Do not merge cells and do not use formulas" in text
    assert "0=OFF;1=ON" in text
    assert "You can type here" in text and "Locked" in text
    for sheet in COLUMNS:
        assert sheet.value in left, sheet


def test_readme_says_when_the_baseline_is_not_known(
    tmp_path: Path, model: Model, config: ProjectConfig
) -> None:
    target = tmp_path / "nobase.xlsx"
    meta = make_meta(model, baseline_commit=None, autosar_schema=None)
    assert XlsxWorkbookIO().export(model, config, target, meta) == ()
    lines = [
        " ".join(str(v) for v in row if v)
        for row in load(target)["README"].iter_rows(values_only=True)
    ]
    assert "Baseline commit (not known)" in lines
    meta_rows = dict(load(target)["Meta"].iter_rows(min_row=2, values_only=True))
    assert meta_rows["baseline_commit"] is None and meta_rows["autosar_schema"] is None


def test_meta_sheet_holds_the_workbook_meta_as_key_value_rows(
    exported: Path, meta: WorkbookMeta
) -> None:
    rows = list(load(exported)["Meta"].iter_rows(values_only=True))
    assert rows == [
        ("Key", "Value"),
        ("tool_version", "0.0.1"),
        ("layout_version", "1"),
        ("autosar_schema", "AUTOSAR_4-2-2.xsd"),
        ("baseline_commit", meta.baseline_commit),
        ("model_hash", meta.model_hash),
        ("export_time_utc", EXPORT_TIME),
    ]


def test_results_sheet_has_one_row_per_request_result_finding(
    tmp_path: Path, model: Model, config: ProjectConfig, meta: WorkbookMeta
) -> None:
    where = RowRef(file=str(tmp_path / "old.xlsx"), sheet="Ports", row=9)
    results = [
        RequestResult(
            request_id="REQ-1",
            status=RequestStatus.REJECTED,
            row=where,
            findings=(
                Finding(code="VAL-NAME-001", severity=Severity.ERROR, message="Bad name."),
                Finding(code="VAL-REF-001", severity=Severity.ERROR, message="No interface."),
            ),
        ),
        RequestResult(request_id="REQ-2", status=RequestStatus.APPLIED, row=where),
    ]
    target = tmp_path / "with_results.xlsx"
    assert XlsxWorkbookIO().export(model, config, target, meta, results) == ()
    rows = list(load(target)["Results"].iter_rows(values_only=True))
    assert rows == [
        ("RowID", "Sheet", "Status", "Code", "Reason"),
        ("REQ-1", "Ports", "rejected", "VAL-NAME-001", "Bad name. [old.xlsx, row 9]"),
        ("REQ-1", "Ports", "rejected", "VAL-REF-001", "No interface. [old.xlsx, row 9]"),
        ("REQ-2", "Ports", "applied", None, "[old.xlsx, row 9]"),
    ]


# --------------------------------------------------------------------------- text safety


def awkward_model() -> Model:
    names = ["=cmd|' /C calc'!A0", "+1+1", "-2+3", "@SUM(A1)", "Größe_Öl", "速度センサ", "Плата"]
    return Model(
        components=[
            Component(
                path=f"{CT}/Swc{index}",
                name=name,
                kind=ComponentKind.APPLICATION,
                team="Équipe Ω",
            )
            for index, name in enumerate(names)
        ],
        ports=[
            Port(
                path=f"{CT}/Swc{index}/Port{index}",
                name=name,
                swc_path=f"{CT}/Swc{index}",
                direction=PortDirection.P,
                interface_path=None,
            )
            for index, name in enumerate(names)
        ],
    )


def test_a_leading_formula_character_in_a_name_stays_text(
    tmp_path: Path, config: ProjectConfig
) -> None:
    model = awkward_model()
    target = tmp_path / "awkward.xlsx"
    assert XlsxWorkbookIO().export(model, config, target, make_meta(model)) == ()
    worksheet = load(target)["Ports"]
    cells = [worksheet.cell(row, column_of(Sheet.PORTS, "port")) for row in range(2, 9)]
    assert {cell.value for cell in cells} == {port.name for port in model.ports.values()}
    assert {cell.data_type for cell in cells} == {"s"}
    with zipfile.ZipFile(target) as archive:
        for name in archive.namelist():
            if name.startswith("xl/worksheets/"):
                assert "<f>" not in archive.read(name).decode("utf-8"), name
    swcs = [cell.value for cell in load(target)["Lists"]["M"][1:] if cell.value is not None]
    assert "=cmd|' /C calc'!A0" in swcs and "@SUM(A1)" in swcs


def test_non_ascii_names_are_written_unchanged(tmp_path: Path, config: ProjectConfig) -> None:
    model = awkward_model()
    target = tmp_path / "Größe 速度.xlsx"
    assert XlsxWorkbookIO().export(model, config, target, make_meta(model)) == ()
    components = load(target)["Components"]
    names = {row[0] for row in components.iter_rows(min_row=2, values_only=True)}
    assert {"Größe_Öl", "速度センサ", "Плата"} <= names
    teams = {row[4] for row in components.iter_rows(min_row=2, values_only=True)}
    assert teams == {"Équipe Ω"}


# --------------------------------------------------------------------------- determinism


def test_two_exports_of_the_same_model_are_byte_identical(
    tmp_path: Path, model: Model, config: ProjectConfig, meta: WorkbookMeta
) -> None:
    first, second = tmp_path / "a" / "first.xlsx", tmp_path / "b" / "second.xlsx"
    first.parent.mkdir()
    second.parent.mkdir()
    assert XlsxWorkbookIO().export(model, config, first, meta) == ()
    assert XlsxWorkbookIO().export(model, config, second, meta) == ()
    assert first.read_bytes() == second.read_bytes()


def test_exports_differ_only_where_the_export_time_is_written(
    tmp_path: Path, model: Model, config: ProjectConfig, meta: WorkbookMeta
) -> None:
    first, second = tmp_path / "first.xlsx", tmp_path / "second.xlsx"
    later = make_meta(model, export_time_utc="2027-01-02T03:04:05Z")
    assert XlsxWorkbookIO().export(model, config, first, meta) == ()
    assert XlsxWorkbookIO().export(model, config, second, later) == ()
    with zipfile.ZipFile(first) as one, zipfile.ZipFile(second) as two:
        assert one.namelist() == two.namelist()
        different = {name for name in one.namelist() if one.read(name) != two.read(name)}
        # The time is text: it lives in the shared strings table, nowhere else.
        assert different == {"xl/sharedStrings.xml"}
        strings = one.read("xl/sharedStrings.xml").decode("utf-8")
        assert strings.replace(EXPORT_TIME, "2027-01-02T03:04:05Z") == two.read(
            "xl/sharedStrings.xml"
        ).decode("utf-8")
        assert {info.date_time for info in one.infolist()} == {(1980, 1, 1, 0, 0, 0)}


def test_creation_date_in_the_file_is_fixed(exported: Path) -> None:
    with zipfile.ZipFile(exported) as archive:
        core = archive.read("docProps/core.xml").decode("utf-8")
    assert ">2020-01-01T00:00:00Z</dcterms:created>" in core
    assert ">2020-01-01T00:00:00Z</dcterms:modified>" in core


_EXPORT_SCRIPT = """
import sys
from pathlib import Path
from porttool.workbook import XlsxWorkbookIO
from tests.helpers.sample_model import build_sample_config, build_sample_model
from tests.unit.workbook.helpers import make_meta

model = build_sample_model()
target = Path(sys.argv[1])
config = build_sample_config(target.parent)
findings = XlsxWorkbookIO().export(model, config, target, make_meta(model))
assert findings == (), findings
"""


def test_export_is_byte_identical_in_another_process_with_another_hash_seed(
    tmp_path: Path, exported_master: Path
) -> None:
    repo = Path(__file__).resolve().parents[3]
    for seed in ("1", "4242"):
        target = tmp_path / f"seed{seed}.xlsx"
        environment = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(repo)}
        subprocess.run(  # noqa: S603 - fixed argument list, our own interpreter
            [sys.executable, "-c", _EXPORT_SCRIPT, str(target)],
            check=True,
            cwd=repo,
            env=environment,
            timeout=120,
        )
        assert target.read_bytes() == exported_master.read_bytes(), seed


# --------------------------------------------------------------------------- limits


def test_more_rows_than_excel_allows_returns_finding_and_writes_nothing(
    tmp_path: Path, model: Model, config: ProjectConfig, meta: WorkbookMeta
) -> None:
    target = tmp_path / "toobig.xlsx"
    # The sample model has 6 ports: with the header that is 7 rows. Excel's real limit
    # is 1,048,576; the limit is lowered here instead of building a million objects.
    workbook_io = XlsxWorkbookIO(ExportOptions(max_rows=6))
    findings = workbook_io.export(model, config, target, meta)
    # Only the sheets that do not fit are reported (the longest dropdown list is one).
    assert codes_of(findings) == [codes.EXPORT_TOO_MANY_ROWS, codes.EXPORT_TOO_MANY_ROWS]
    assert {finding.severity for finding in findings} == {Severity.ERROR}
    assert "'Ports' needs 7 rows. Excel allows 6 rows" in findings[0].message
    assert "'Lists' needs 12 rows" in findings[1].message
    assert findings[0].file == str(target)
    assert not target.exists()
    assert temp_files(tmp_path) == []
    assert ExportOptions().max_rows == 1_048_576


def test_a_too_long_dropdown_list_counts_as_too_many_rows(
    tmp_path: Path, model: Model, config: ProjectConfig, meta: WorkbookMeta
) -> None:
    # 11 access kinds plus the header need 12 rows on the Lists sheet.
    findings = XlsxWorkbookIO(ExportOptions(max_rows=11, blank_rows=0)).export(
        model, config, tmp_path / "lists.xlsx", meta
    )
    assert codes_of(findings) == [codes.EXPORT_TOO_MANY_ROWS]
    assert "'Lists' needs 12 rows" in findings[0].message


def test_blank_rows_are_reduced_when_the_row_limit_is_near(
    tmp_path: Path, model: Model, config: ProjectConfig, meta: WorkbookMeta
) -> None:
    target = tmp_path / "tight.xlsx"
    findings = XlsxWorkbookIO(ExportOptions(max_rows=12)).export(model, config, target, meta)
    assert set(codes_of(findings)) == {codes.EXPORT_BLANK_ROWS_REDUCED}
    assert {finding.severity for finding in findings} == {Severity.WARNING}
    assert len(findings) == len(REQUEST_SHEETS)
    ports = next(finding for finding in findings if "'Ports'" in finding.message)
    assert "room for 5 blank rows only, not 500" in ports.message
    workbook = load(target)
    assert workbook["Ports"].max_row == 12
    assert workbook["Connections"].max_row == 12


def long_enum_model(length: int) -> Model:
    values = tuple((index, f"VALUE_{index:05d}") for index in range(length))
    return Model(
        compu_methods=[CompuMethod(path=f"{DT}/CM/Big", name="CM_Big", enum_values=values)],
        data_types=[
            DataType(
                path=f"{DT}/Big",
                name="Big",
                level=DataTypeLevel.APPLICATION,
                category=DataTypeCategory.ENUMERATION,
                compu_method_path=f"{DT}/CM/Big",
            )
        ],
    )


def test_cell_above_32767_characters_is_truncated_with_finding(
    tmp_path: Path, config: ProjectConfig
) -> None:
    model = long_enum_model(3000)  # "0=VALUE_00000;..." is far above the limit
    target = tmp_path / "long.xlsx"
    findings = XlsxWorkbookIO().export(model, config, target, make_meta(model))
    assert codes_of(findings) == [codes.EXPORT_CELL_TRUNCATED]
    finding = findings[0]
    assert finding.severity is Severity.WARNING
    assert finding.object_path == f"{DT}/Big"
    assert finding.row == RowRef(file=str(target), sheet="DataTypes", row=2)
    assert "Excel allows 32767" in finding.message
    cell = load(target)["DataTypes"].cell(2, column_of(Sheet.DATA_TYPES, "enum_values"))
    assert len(cell.value) == 32_767
    assert cell.value.startswith("0=VALUE_00000;1=VALUE_00001;")


def test_cell_of_exactly_32767_characters_is_not_truncated(
    tmp_path: Path, config: ProjectConfig
) -> None:
    name = "N" * 32_767
    model = Model(components=[Component(path="/P/X", name=name, kind=ComponentKind.OTHER)])
    target = tmp_path / "exact.xlsx"
    assert XlsxWorkbookIO().export(model, config, target, make_meta(model)) == ()
    assert load(target)["Components"]["A2"].value == name
    longer = Model(components=[Component(path="/P/X", name=name + "!", kind=ComponentKind.OTHER)])
    findings = XlsxWorkbookIO().export(longer, config, target, make_meta(longer))
    # The name is on the Components sheet and in the dropdown list of SWC names.
    assert codes_of(findings) == [codes.EXPORT_CELL_TRUNCATED, codes.EXPORT_CELL_TRUNCATED]
    assert [finding.row.sheet for finding in findings if finding.row] == ["Components", "Lists"]


# --------------------------------------------------------------------------- saving


def test_export_replaces_an_existing_target_and_leaves_no_temporary_file(
    tmp_path: Path, model: Model, config: ProjectConfig, meta: WorkbookMeta
) -> None:
    target = tmp_path / "requests.xlsx"
    target.write_bytes(b"old content")
    assert XlsxWorkbookIO().export(model, config, target, meta) == ()
    assert zipfile.is_zipfile(target)
    assert sorted(entry.name for entry in tmp_path.iterdir()) == ["requests.xlsx"]


def test_temporary_file_is_created_in_the_target_folder(
    tmp_path: Path,
    model: Model,
    config: ProjectConfig,
    meta: WorkbookMeta,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    moves: list[tuple[Path, Path]] = []

    def record(source: Path, destination: Path) -> None:
        assert source.exists() and zipfile.is_zipfile(source)
        moves.append((source, destination))
        os.replace(source, destination)

    monkeypatch.setattr(exporter, "_replace", record)
    target = tmp_path / "requests.xlsx"
    assert XlsxWorkbookIO().export(model, config, target, meta) == ()
    assert len(moves) == 1
    source, destination = moves[0]
    assert destination == target
    assert source.parent == target.parent
    assert source.name.startswith("requests.") and source.suffix == ".tmp"


def test_export_folder_missing_returns_finding(
    tmp_path: Path, model: Model, config: ProjectConfig, meta: WorkbookMeta
) -> None:
    target = tmp_path / "no" / "such" / "folder" / "requests.xlsx"
    findings = XlsxWorkbookIO().export(model, config, target, meta)
    assert codes_of(findings) == [codes.EXPORT_FOLDER_MISSING]
    assert findings[0].severity is Severity.ERROR
    assert not target.parent.exists()  # the export never creates folders


def test_export_folder_that_is_a_file_returns_finding(
    tmp_path: Path, model: Model, config: ProjectConfig, meta: WorkbookMeta
) -> None:
    (tmp_path / "blocker").write_text("not a folder")
    findings = XlsxWorkbookIO().export(model, config, tmp_path / "blocker" / "r.xlsx", meta)
    assert codes_of(findings) == [codes.EXPORT_FOLDER_MISSING]


def test_export_folder_read_only_returns_finding(
    tmp_path: Path,
    model: Model,
    config: ProjectConfig,
    meta: WorkbookMeta,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The tests may run as an administrator who can write anywhere, so the refusal of the
    # operating system is simulated where it would surface: creating the temporary file.
    def refuse(target: Path) -> tuple[IO[bytes], Path]:
        raise PermissionError(errno.EACCES, "Permission denied", str(target.parent))

    monkeypatch.setattr(exporter, "_open_temp", refuse)
    target = tmp_path / "requests.xlsx"
    findings = XlsxWorkbookIO().export(model, config, target, meta)
    assert codes_of(findings) == [codes.EXPORT_FOLDER_READ_ONLY]
    assert findings[0].severity is Severity.ERROR
    assert str(tmp_path) in findings[0].message
    assert not target.exists()


def test_export_folder_vanishing_during_export_returns_finding(
    tmp_path: Path,
    model: Model,
    config: ProjectConfig,
    meta: WorkbookMeta,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def gone(target: Path) -> tuple[IO[bytes], Path]:
        raise FileNotFoundError(errno.ENOENT, "No such file or directory", str(target.parent))

    monkeypatch.setattr(exporter, "_open_temp", gone)
    findings = XlsxWorkbookIO().export(model, config, tmp_path / "requests.xlsx", meta)
    assert codes_of(findings) == [codes.EXPORT_FOLDER_MISSING]


def test_workbook_open_in_excel_on_write_returns_finding_and_keeps_the_old_file(
    tmp_path: Path,
    model: Model,
    config: ProjectConfig,
    meta: WorkbookMeta,
    io: XlsxWorkbookIO,
    sleeper: SleepRecorder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[Path] = []

    def locked(source: Path, destination: Path) -> None:
        attempts.append(source)
        raise PermissionError(errno.EACCES, "The process cannot access the file", str(destination))

    monkeypatch.setattr(exporter, "_replace", locked)
    target = tmp_path / "requests.xlsx"
    target.write_bytes(b"the workbook Excel has open")
    findings = io.export(model, config, target, meta)
    assert codes_of(findings) == [codes.EXPORT_TARGET_LOCKED]
    assert findings[0].severity is Severity.ERROR
    assert "open in Excel or is read-only" in findings[0].message
    assert findings[0].file == str(target)
    assert len(attempts) == 4  # the first try and three more
    assert sleeper.waits == [0.1, 0.2, 0.4]
    assert target.read_bytes() == b"the workbook Excel has open"
    assert temp_files(tmp_path) == []


def test_locked_target_is_retried_and_the_export_succeeds_when_it_is_released(
    tmp_path: Path,
    model: Model,
    config: ProjectConfig,
    meta: WorkbookMeta,
    io: XlsxWorkbookIO,
    sleeper: SleepRecorder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def locked_twice(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise PermissionError(errno.EACCES, "sharing violation", str(destination))
        os.replace(source, destination)

    monkeypatch.setattr(exporter, "_replace", locked_twice)
    target = tmp_path / "requests.xlsx"
    assert io.export(model, config, target, meta) == ()
    assert calls == 3
    assert sleeper.waits == [0.1, 0.2]
    assert zipfile.is_zipfile(target)


class FullDisk:
    """A file that accepts ``room`` bytes and then reports a full disk."""

    def __init__(self, real: IO[bytes], room: int) -> None:
        self._real = real
        self._room = room

    def write(self, data: bytes) -> int:
        if len(data) > self._room:
            self._real.write(data[: self._room])
            raise OSError(errno.ENOSPC, "No space left on device")
        self._room -= len(data)
        return self._real.write(data)

    def __enter__(self) -> FullDisk:
        return self

    def __exit__(self, *details: object) -> None:
        self._real.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def test_disk_full_during_save_returns_finding_and_removes_the_partial_file(
    tmp_path: Path,
    model: Model,
    config: ProjectConfig,
    meta: WorkbookMeta,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_open = exporter._open_temp
    created: list[Path] = []

    def small_disk(target: Path) -> tuple[IO[bytes], Path]:
        handle, temp = real_open(target)
        created.append(temp)
        return FullDisk(handle, room=1000), temp  # type: ignore[return-value]

    monkeypatch.setattr(exporter, "_open_temp", small_disk)
    target = tmp_path / "requests.xlsx"
    target.write_bytes(b"previous export")
    findings = XlsxWorkbookIO().export(model, config, target, meta)
    assert codes_of(findings) == [codes.EXPORT_DISK_FULL]
    assert findings[0].severity is Severity.ERROR
    assert "disk is full" in findings[0].message
    assert len(created) == 1 and not created[0].exists()
    assert target.read_bytes() == b"previous export"
    assert temp_files(tmp_path) == []


def test_disk_full_while_flushing_to_disk_returns_finding(
    tmp_path: Path,
    model: Model,
    config: ProjectConfig,
    meta: WorkbookMeta,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def full(descriptor: int) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(os, "fsync", full)
    target = tmp_path / "requests.xlsx"
    findings = XlsxWorkbookIO().export(model, config, target, meta)
    assert codes_of(findings) == [codes.EXPORT_DISK_FULL]
    assert not target.exists() and temp_files(tmp_path) == []


def test_other_operating_system_error_during_save_returns_finding(
    tmp_path: Path,
    model: Model,
    config: ProjectConfig,
    meta: WorkbookMeta,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(source: Path, destination: Path) -> None:
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(exporter, "_replace", broken)
    target = tmp_path / "requests.xlsx"
    findings = XlsxWorkbookIO().export(model, config, target, meta)
    assert codes_of(findings) == [codes.EXPORT_IO_ERROR]
    assert "Input/output error" in findings[0].message
    assert not target.exists() and temp_files(tmp_path) == []


def test_temporary_file_that_cannot_be_removed_is_reported(
    tmp_path: Path,
    model: Model,
    config: ProjectConfig,
    meta: WorkbookMeta,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(source: Path, destination: Path) -> None:
        raise OSError(errno.EIO, "Input/output error")

    def stuck(path: Any) -> None:
        raise PermissionError(errno.EACCES, "Access is denied")

    monkeypatch.setattr(exporter, "_replace", broken)
    monkeypatch.setattr(os, "unlink", stuck)
    findings = XlsxWorkbookIO().export(model, config, tmp_path / "requests.xlsx", meta)
    assert codes_of(findings) == [codes.EXPORT_IO_ERROR, codes.EXPORT_TEMP_LEFT_BEHIND]
    assert findings[1].severity is Severity.WARNING
    assert len(temp_files(tmp_path)) == 1
    assert temp_files(tmp_path)[0] in findings[1].message


def test_cell_findings_are_dropped_when_nothing_was_written(
    tmp_path: Path, config: ProjectConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def locked(source: Path, destination: Path) -> None:
        raise PermissionError(errno.EACCES, "locked")

    monkeypatch.setattr(exporter, "_replace", locked)
    model = long_enum_model(3000)
    workbook_io = XlsxWorkbookIO(ExportOptions(retry_delays_s=()))
    findings = workbook_io.export(model, config, tmp_path / "r.xlsx", make_meta(model))
    assert codes_of(findings) == [codes.EXPORT_TARGET_LOCKED]


def test_target_must_be_an_xlsx_file(
    tmp_path: Path, model: Model, config: ProjectConfig, meta: WorkbookMeta
) -> None:
    for name in ("requests.xls", "requests.xlsm", "requests.csv", "requests"):
        findings = XlsxWorkbookIO().export(model, config, tmp_path / name, meta)
        assert codes_of(findings) == [codes.EXPORT_BAD_EXTENSION], name
        assert not (tmp_path / name).exists()
    assert XlsxWorkbookIO().export(model, config, tmp_path / "UPPER.XLSX", meta) == ()


@pytest.mark.windows_only
def test_read_only_target_file_returns_finding_on_windows(
    tmp_path: Path, model: Model, config: ProjectConfig, meta: WorkbookMeta
) -> None:
    """On Windows ``os.replace`` refuses a read-only target. Not provable on this platform."""
    if sys.platform != "win32":
        pytest.skip("needs Windows: a read-only file is replaceable on POSIX")
    target = tmp_path / "requests.xlsx"
    target.write_bytes(b"read-only")
    target.chmod(0o444)
    findings = XlsxWorkbookIO(ExportOptions(retry_delays_s=())).export(model, config, target, meta)
    assert codes_of(findings) == [codes.EXPORT_TARGET_LOCKED]


# --------------------------------------------------------------------------- unexpected


def test_a_call_xlsxwriter_refuses_is_a_workbook_error() -> None:
    exporter._check(0, "nothing")
    with pytest.raises(WorkbookError) as raised:
        exporter._check(-1, "a cell on sheet Ports")
    assert "a cell on sheet Ports" in (raised.value.detail or "")
    assert "status -1" in (raised.value.detail or "")


def test_an_xlsxwriter_exception_is_a_workbook_error_and_writes_nothing(
    tmp_path: Path,
    model: Model,
    config: ProjectConfig,
    meta: WorkbookMeta,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(*arguments: object) -> None:
        raise XlsxWriterException("boom")

    monkeypatch.setattr(exporter, "_write_meta", explode)
    target = tmp_path / "requests.xlsx"
    with pytest.raises(WorkbookError) as raised:
        XlsxWorkbookIO().export(model, config, target, meta)
    assert raised.value.detail == "XlsxWriterException: boom"
    assert not target.exists() and temp_files(tmp_path) == []


def test_sheet_xml_has_no_inline_strings(exported: Path) -> None:
    # Shared strings keep the file small and the import fast.
    assert "inlineStr" not in sheet_xml(exported, Sheet.PORTS)
