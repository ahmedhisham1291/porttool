"""Properties of the outcome as a whole: order, ids, purity, protocol, command line."""

from __future__ import annotations

import click
from click.testing import CliRunner

from porttool.core.interfaces import Validator
from porttool.core.operations import APPLY_ORDER, AddTrigger, ChangeSet, Route
from porttool.core.pending import PendingQueue
from porttool.core.results import RequestStatus
from porttool.validator import (
    ALL_CODES,
    CODES,
    ChangeValidator,
    describe,
    format_code_table,
    register_cli,
    request_id_of,
    validate,
)
from porttool.validator import codes as c
from porttool.validator._world import World
from porttool.validator.validator import _unique_operations
from tests.unit.validator.support import (
    ALPHA,
    CONFIG,
    MODEL,
    NOW,
    access_rid,
    access_row,
    connection_row,
    connector_rid,
    element_row,
    interface_row,
    number,
    op_kinds,
    port_rid,
    port_row,
    run,
    statuses,
    trigger_rid,
    trigger_row,
    type_row,
)

EVERYTHING = [
    connection_row("SwcA", "PpNew", "SwcC", "RpNewC"),
    trigger_row("SwcA", "RunA_10ms", "periodic", period_ms="20"),
    access_row("SwcA", "RunA_10ms", "PpNew", "V", "implicit write"),
    port_row("SwcA", "PpDiagNew", "P", "IfDiag", ""),
    port_row(
        "SwcA", "RpTorque", "R", "IfTorque", "9", action="MODIFY", rid=port_rid("SwcA", "RpTorque")
    ),
    port_row("SwcA", "PpNew", "P", "IfNew", "1"),
    element_row("IfNew", "V", "NewT"),
    interface_row("IfNew"),
    type_row("NewT", base_type="uint8"),
    port_row("SwcC", "RpNewC", "R", "IfNew", "1"),
    port_row("SwcB", "RpSpeed", action="DELETE", rid=port_rid("SwcB", "RpSpeed")),
    access_row(
        "SwcB",
        "RunB_OnSpeed",
        "RpSpeed",
        "Speed",
        "explicit read",
        action="DELETE",
        rid=access_rid("SwcB", "RunB_OnSpeed", "DRPA_RpSpeed_Speed"),
    ),
    trigger_row(
        "SwcB",
        "RunB_OnSpeed",
        "on data reception",
        "RpSpeed",
        "Speed",
        action="DELETE",
        rid=trigger_rid("SwcB", "DRT_RunB_OnSpeed"),
    ),
    connection_row(
        "SwcA",
        "PpSpeed",
        "SwcB",
        "RpSpeed",
        action="DELETE",
        rid=connector_rid("SwcA_PpSpeed_SwcB_RpSpeed"),
    ),
]


def test_change_set_holds_every_operation_kind_in_apply_order() -> None:
    outcome = run(*EVERYTHING)
    assert set(statuses(outcome)) == {RequestStatus.ACCEPTED}
    assert op_kinds(outcome) == [
        "delete_connector",
        "delete_trigger",
        "delete_access_point",
        "delete_port",
        "add_data_type",
        "add_interface",
        "add_port",
        "add_port",
        "add_port",
        "modify_port",
        "add_server_runnable",
        "add_access_point",
        "add_trigger",
        "add_trigger",
        "add_connector",
    ]
    assert set(op_kinds(outcome)) == set(APPLY_ORDER)
    # Stable inside one kind: the row order of the import.
    assert [op.name for op in outcome.change_set.operations if op.op == "add_port"] == [
        "PpDiagNew",
        "PpNew",
        "RpNewC",
    ]
    triggers = [op for op in outcome.change_set.operations if isinstance(op, AddTrigger)]
    assert [t.runnable_name for t in triggers] == ["RunA_10ms", "PpDiagNew_ReadDtc"]


def test_operation_ids_are_deterministic_and_carry_the_source() -> None:
    first = run(*EVERYTHING)
    second = run(*reversed(EVERYTHING))
    assert {op.id for op in first.change_set.operations} == {
        op.id for op in second.change_set.operations
    }
    port = next(
        op
        for op in first.change_set.operations
        if op.id == "Ports:add_port:/ComponentTypes/SwcA/PpNew"
    )
    assert (port.source_file, port.source_sheet, port.requester) == (ALPHA, "Ports", "alice")
    assert all(op.id.split(":")[1] == op.op for op in first.change_set.operations)
    assert first.results[0].request_id == f"{ALPHA}#Connections#2"
    assert request_id_of(number([port_row("SwcA", "X")])[0]) == f"{ALPHA}#Ports#2"


def test_validate_is_pure() -> None:
    rows = number(EVERYTHING)
    queue = PendingQueue()
    first = validate(MODEL, CONFIG, rows, queue, NOW)
    second = validate(MODEL, CONFIG, rows, queue, NOW)
    assert first == second
    assert first.change_set.to_json() == second.change_set.to_json()
    assert queue == PendingQueue()
    assert ChangeSet.from_json(first.change_set.to_json()) == first.change_set


def test_change_validator_implements_the_validator_protocol() -> None:
    validator: Validator = ChangeValidator()
    outcome = validator.validate(
        MODEL, CONFIG, number([port_row("SwcA", "RpNew")]), PendingQueue(), NOW
    )
    assert outcome.results[0].status is RequestStatus.ACCEPTED
    assert outcome.change_set.routed(Route.APPLY)


def test_two_rows_at_the_same_position_keep_unique_request_ids() -> None:
    row = number([port_row("SwcA", "RpNew")])[0]
    outcome = validate(MODEL, CONFIG, [row, row], PendingQueue(), NOW)
    assert [r.request_id for r in outcome.results] == [f"{ALPHA}#Ports#2", f"{ALPHA}#Ports#2~2"]
    assert len(outcome.change_set.operations) == 1


def test_operation_id_collisions_are_resolved() -> None:
    world = World(MODEL, CONFIG, NOW)
    rows = number([port_row("SwcA", "RpOne"), port_row("SwcA", "RpTwo")])
    outcome = validate(MODEL, CONFIG, rows, PendingQueue(), NOW)
    op = outcome.change_set.operations[0]
    from porttool.validator._state import Req

    reqs = [Req(index=i, raw=row, request_id=str(i)) for i, row in enumerate(rows)]
    for req in reqs:
        req.emit(op)
    assert [o.id for o in _unique_operations(world, reqs)] == [op.id, f"{op.id}~2"]


def test_code_table_and_command_line() -> None:
    assert len(ALL_CODES) == len(set(ALL_CODES)) == len(CODES)
    assert all(code.startswith("VAL-") and CODES[code].description for code in ALL_CODES)
    assert describe(c.ROW_UNKNOWN_SHEET) and describe("VAL-NOPE-000") == ""
    assert c.severity_of("VAL-NOPE-000").value == "error"
    table = format_code_table()
    assert all(code in table for code in ALL_CODES)

    @click.group()
    def app() -> None:
        """Test application."""

    register_cli(app)
    result = CliRunner().invoke(app, ["validator-codes"])
    assert result.exit_code == 0
    assert "VAL-ROW-001" in result.output and "VAL-CFG-002" in result.output
