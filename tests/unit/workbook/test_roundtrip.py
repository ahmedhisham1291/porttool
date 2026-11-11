"""Round trip: export the sample model, edit the file by script, import the edits."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from porttool.core.config import ProjectConfig
from porttool.core.models import Model
from porttool.core.results import RawRequestRow, RowRef
from porttool.core.sheets import COLUMNS, REQUEST_SHEETS, Sheet, WorkbookMeta
from porttool.workbook import XlsxWorkbookIO
from porttool.workbook.rows import build_all_rows, build_labels
from tests.unit.workbook.helpers import column_of, edit, first_blank_row, set_cells

#: What a team types into the first blank row of each request sheet.
NEW_ROWS: dict[Sheet, dict[str, Any]] = {
    Sheet.DATA_TYPES: {
        "name": "Dt_Gear",
        "level": "application",
        "category": "enumeration",
        "base_type": "uint16",
        "lower_limit": 0,
        "upper_limit": 3,
        "enum_values": "0=P;1=R;2=N;3=D",
        "action": "ADD",
        "requester": "Ada",
        "comment": "new gear type",
    },
    Sheet.INTERFACES: {
        "name": "IfGear",
        "kind": "sender-receiver",
        "action": "ADD",
        "requester": "Ada",
    },
    Sheet.INTERFACE_ELEMENTS: {
        "interface": "IfGear",
        "element": "Gear",
        "data_type": "Dt_Gear",
        "queued": "N",
        "action": "ADD",
    },
    Sheet.PORTS: {
        "swc": "SwcA",
        "port": "PpGear",
        "direction": "P",
        "interface": "IfGear",
        "init_value": "Gear=0",
        "action": "ADD",
        "requester": "Ada",
        "comment": "=not a formula",
    },
    Sheet.RUNNABLE_ACCESS: {
        "swc": "SwcA",
        "runnable": "RunA_10ms",
        "port": "PpGear",
        "target": "Gear",
        "access_kind": "implicit write",
        "action": "ADD",
    },
    Sheet.RUNNABLE_TRIGGERS: {
        "swc": "SwcB",
        "runnable": "RunB_OnSpeed",
        "trigger_kind": "periodic",
        "period_ms": 20,
        "task": "Task_10ms",
        "action": "ADD",
    },
    Sheet.CONNECTIONS: {
        "provider_swc": "SwcM",
        "provider_port": "PpTorque",
        "requester_swc": "SwcA",
        "requester_port": "RpTorque",
        "composition": "EcuComposition",
        "action": "ADD",
        "requester": "Grace",
        "comment": "connect the torque path",
    },
}

#: Action set on the first existing row of each request sheet.
EXISTING_ACTIONS: dict[Sheet, str] = {
    Sheet.DATA_TYPES: "MODIFY",
    Sheet.INTERFACES: "DELETE",
    Sheet.INTERFACE_ELEMENTS: "DELETE",
    Sheet.PORTS: "MODIFY",
    Sheet.RUNNABLE_ACCESS: "DELETE",
    Sheet.RUNNABLE_TRIGGERS: "MODIFY",
    Sheet.CONNECTIONS: "DELETE",
}


def as_text(value: object) -> str:
    return value if isinstance(value, str) else str(value)


def test_round_trip_returns_exactly_the_edited_rows(
    io: XlsxWorkbookIO, exported: Path, model: Model, config: ProjectConfig, meta: WorkbookMeta
) -> None:
    assert set(NEW_ROWS) == set(REQUEST_SHEETS) == set(EXISTING_ACTIONS)
    blank_rows: dict[Sheet, int] = {}

    def change(workbook: Any) -> None:
        for sheet in REQUEST_SHEETS:
            worksheet = workbook[sheet.value]
            set_cells(
                worksheet,
                sheet,
                2,
                action=EXISTING_ACTIONS[sheet],
                requester="Team Beta",
                comment=f"please {EXISTING_ACTIONS[sheet].lower()}",
            )
            blank_rows[sheet] = first_blank_row(worksheet)
            set_cells(worksheet, sheet, blank_rows[sheet], **NEW_ROWS[sheet])
        # Excel stores what is typed into a text-formatted cell as text, even "=...".
        # openpyxl would make it a formula, so the script states the cell type.
        comment = workbook["Ports"].cell(blank_rows[Sheet.PORTS], column_of(Sheet.PORTS, "comment"))
        assert comment.number_format == "@"
        comment.data_type = "s"

    path = edit(exported, exported.with_name("edited.xlsx"), change)
    imported = io.import_rows([path], meta)

    exported_rows = build_all_rows(model, config, (), build_labels(model), partner_limit=10)
    expected: list[RawRequestRow] = []
    for sheet in REQUEST_SHEETS:
        keys = [column.key for column in COLUMNS[sheet]]
        action = EXISTING_ACTIONS[sheet]
        existing = dict(zip(keys, map(as_text, exported_rows[sheet].rows[0]), strict=True))
        existing.update(action=action, requester="Team Beta", comment=f"please {action.lower()}")
        expected.append(
            RawRequestRow(
                sheet=sheet.value,
                row=RowRef(file=str(path), sheet=sheet.value, row=2),
                row_id=existing["row_id"],
                action=action,
                requester="Team Beta",
                comment=f"please {action.lower()}",
                values=existing,
            )
        )
        typed = {key: as_text(NEW_ROWS[sheet].get(key, "")) for key in keys}
        expected.append(
            RawRequestRow(
                sheet=sheet.value,
                row=RowRef(file=str(path), sheet=sheet.value, row=blank_rows[sheet]),
                row_id=None,
                action="ADD",
                requester=typed["requester"],
                comment=typed["comment"],
                values=typed,
            )
        )

    assert imported.findings == ()
    assert list(imported.rows) == expected
    assert imported.metas == ((str(path), meta),)
    for row in imported.rows:
        assert list(row.values) == [column.key for column in COLUMNS[Sheet(row.sheet)]]
        assert all(isinstance(value, str) for value in row.values.values())
    # Spot checks against literal values, independent of the row builders.
    ports = [row for row in imported.rows if row.sheet == "Ports"]
    assert ports[0].row_id is not None and ports[0].row_id.startswith("PT-")
    assert (ports[0].values["swc"], ports[0].values["port"]) == ("SwcA", "PpSpeed")
    assert ports[0].values["partner_count"] == "1"
    assert ports[1].row.row == 8
    assert ports[1].comment == "=not a formula"
    triggers = [row for row in imported.rows if row.sheet == "RunnableTriggers"]
    assert triggers[0].values["period_ms"] == "10"
    assert triggers[1].values["period_ms"] == "20"


def test_export_of_an_imported_and_reexported_model_keeps_row_ids(
    tmp_path: Path, model: Model, config: ProjectConfig, meta: WorkbookMeta, exported: Path
) -> None:
    """RowIDs depend on the object paths only: a second export names every row the same."""
    again = tmp_path / "again.xlsx"
    assert XlsxWorkbookIO().export(model, config, again, meta) == ()
    assert again.read_bytes() == exported.read_bytes()
