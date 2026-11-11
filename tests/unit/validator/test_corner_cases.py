"""One named test for every corner case of the validator task."""

from __future__ import annotations

import time

from porttool.core.models import PortDirection, TriggerKind
from porttool.core.operations import (
    AddConnector,
    AddDataType,
    AddInterface,
    AddPort,
    AddServerRunnable,
    AddTrigger,
    DeletePort,
    Route,
)
from porttool.core.pending import PendingQueue, PendingState
from porttool.core.results import RequestStatus, Severity
from porttool.validator import codes as c
from tests.helpers.sample_model import COMPOSITION, CT, DT, IF
from tests.unit.validator.support import (
    BETA,
    MODEL,
    A,
    D,
    H,
    R,
    W,
    access_row,
    behavior,
    codes_of,
    connection_row,
    element_row,
    extend_model,
    interface_row,
    messages,
    new_port,
    op_kinds,
    operation,
    port_rid,
    port_row,
    raw_row,
    run,
    statuses,
    trigger_row,
    type_row,
)

HELD = connection_row("SwcM", "PpNew", "SwcA", "RpTorque")
MODEL_PORT = port_row("SwcM", "PpNew", "P", "IfTorque", "0")


def test_two_teams_add_the_same_port() -> None:
    outcome = run(port_row("SwcA", "RpNew"), port_row("SwcA", "RpNew", file=BETA, requester="bob"))
    assert statuses(outcome) == (A, A)
    assert op_kinds(outcome) == ["add_port"]
    first, second = outcome.results
    assert first.operation_ids == second.operation_ids
    assert c.CFL_MERGED in {f.code for f in second.findings}
    op = operation(outcome, "add_port")
    assert isinstance(op, AddPort)
    assert (op.requester, op.source_row) == ("alice", 2)


def test_two_teams_add_the_same_port_name_with_different_interfaces() -> None:
    outcome = run(port_row("SwcA", "RpNew"), port_row("SwcA", "RpNew", "R", "IfTorque", file=BETA))
    assert statuses(outcome) == (R, R)
    assert not outcome.change_set.operations
    for result in outcome.results:
        assert [f.code for f in result.findings] == [c.CFL_DIFFERENT]
    assert "team_beta.xlsx" in outcome.results[0].findings[0].message


def test_add_and_delete_of_one_object_in_the_same_import() -> None:
    outcome = run(
        port_row("SwcD", "PpUnused", "P"),
        port_row("SwcD", "PpUnused", "P", action="DELETE", rid=port_rid("SwcD", "PpUnused")),
    )
    assert statuses(outcome) == (R, R)
    assert codes_of(outcome) == {c.CFL_ADD_DELETE}
    assert not outcome.change_set.operations


def test_connector_to_a_port_that_another_row_deletes() -> None:
    outcome = run(
        connection_row("SwcD", "PpUnused", "SwcC", "RpFree"),
        port_row("SwcD", "PpUnused", "P", action="DELETE", rid=port_rid("SwcD", "PpUnused")),
    )
    assert statuses(outcome) == (R, R)
    assert codes_of(outcome) == {c.CFL_DELETED_PORT}
    assert not outcome.change_set.operations


def test_one_request_repeated_in_two_workbooks() -> None:
    rows = [
        interface_row("IfNew"),
        element_row("IfNew", "V", "uint8"),
        port_row("SwcA", "RpV", "R", "IfNew", "1"),
    ]
    again = [
        interface_row("IfNew", file=BETA),
        element_row("IfNew", "V", "uint8", file=BETA),
        port_row("SwcA", "RpV", "R", "IfNew", "1", file=BETA),
    ]
    outcome = run(*rows, *again)
    assert statuses(outcome) == (A,) * 6
    assert op_kinds(outcome) == ["add_interface", "add_port"]
    assert all(c.CFL_MERGED in {f.code for f in r.findings} for r in outcome.results[3:])


def test_request_the_repo_already_fulfils() -> None:
    outcome = run(
        port_row("SwcA", "PpSpeed", "P", "IfSpeed", "0"),
        connection_row("SwcA", "PpSpeed", "SwcB", "RpSpeed"),
        access_row("SwcA", "RunA_10ms", "PpSpeed", "Speed", "implicit write"),
        trigger_row("SwcB", "RunB_OnSpeed", "on data reception", "RpSpeed", "Speed"),
        interface_row("IfSpeed"),
        type_row("uint8", base_type="uint8"),
    )
    assert statuses(outcome) == (D,) * 6
    assert not outcome.change_set.operations
    assert not outcome.pending.entries
    assert all(not result.operation_ids for result in outcome.results)


def test_row_references_an_object_created_further_down_the_sheet() -> None:
    outcome = run(
        port_row("SwcA", "PpLater", "P", "IfLater", "Value=3;Rec=none"),
        element_row("IfLater", "Value", "LaterU8"),
        element_row("IfLater", "Rec", "LaterRec"),
        interface_row("IfLater"),
        type_row("LaterRec", "record", record_elements="a:LaterU8"),
        type_row("LaterU8", base_type="uint8", upper_limit="10"),
    )
    assert statuses(outcome) == (A,) * 6
    assert op_kinds(outcome) == ["add_data_type", "add_data_type", "add_interface", "add_port"]
    port = operation(outcome, "add_port")
    assert isinstance(port, AddPort)
    assert port.interface_path == f"{IF}/IfLater"
    assert [(v.element, v.value) for v in port.init_values] == [("Value", "3"), ("Rec", None)]


def test_init_value_is_checked_against_a_type_created_in_the_same_import() -> None:
    outcome = run(
        port_row("SwcA", "PpLater", "P", "IfLater", "11"),
        connection_row("SwcA", "PpLater", "SwcC", "RpFree"),
        element_row("IfLater", "Value", "LaterU8"),
        interface_row("IfLater"),
        type_row("LaterU8", base_type="uint8", upper_limit="10"),
    )
    assert statuses(outcome) == (R, R, A, A, A)
    assert [f.code for f in outcome.results[0].findings] == [c.PORT_INIT_RANGE]
    assert [f.code for f in outcome.results[1].findings] == [c.REF_UNKNOWN_PORT]


def test_row_order_does_not_matter() -> None:
    rows = [
        connection_row("SwcA", "PpLater", "SwcB", "RpLater"),
        access_row("SwcA", "RunA_10ms", "PpLater", "Value", "implicit write"),
        port_row("SwcB", "RpLater", "R", "IfLater", "0"),
        port_row("SwcA", "PpLater", "P", "IfLater", "3"),
        element_row("IfLater", "Value", "LaterU8"),
        interface_row("IfLater"),
        type_row("LaterArr", "array", element_type="LaterU8", array_size="2"),
        type_row("LaterU8", base_type="uint8"),
    ]
    forward = run(*rows)
    backward = run(*reversed(rows))
    assert statuses(forward) == (A,) * 8 == statuses(backward)
    expected = [
        "add_data_type",
        "add_data_type",
        "add_interface",
        "add_port",
        "add_port",
        "add_access_point",
        "add_connector",
    ]
    assert op_kinds(forward) == expected == op_kinds(backward)
    assert {op.id for op in forward.change_set.operations} == {
        op.id for op in backward.change_set.operations
    }
    interface = operation(forward, "add_interface")
    assert isinstance(interface, AddInterface)
    assert interface.elements[0].type_path == f"{DT}/LaterU8"
    connector = operation(forward, "add_connector")
    assert isinstance(connector, AddConnector)
    assert connector.route is Route.APPLY
    assert connector.name == "SwcA_PpLater_SwcB_RpLater"


def test_circular_record_types() -> None:
    outcome = run(
        type_row("RecA", "record", record_elements="b:RecB"),
        type_row("RecB", "record", record_elements="a:RecA"),
        type_row("RecFine", "record", record_elements="x:uint8"),
    )
    assert statuses(outcome) == (R, R, A)
    assert {f.code for r in outcome.results[:2] for f in r.findings} == {c.DT_CYCLE}
    record = operation(outcome, "add_data_type")
    assert isinstance(record, AddDataType)
    assert record.record_elements[0].type_path == f"{DT}/uint8"


def test_interface_with_no_elements() -> None:
    outcome = run(interface_row("IfEmpty"), interface_row("IfSrvEmpty", "client-server"))
    assert statuses(outcome) == (R, R)
    assert codes_of(outcome) == {c.IF_EMPTY}


def test_server_port_whose_operation_already_has_a_runnable_in_the_same_import() -> None:
    outcome = run(
        port_row("SwcA", "PpDiagNew", "P", "IfDiag2", ""),
        trigger_row("SwcA", "RunReadDtc", "on operation invocation", "PpDiagNew", "ReadDtc", task="Task_Event"),
    )
    assert statuses(outcome) == (A, A)
    runnables = [op for op in outcome.change_set.operations if isinstance(op, AddServerRunnable)]
    assert sorted((op.name, op.symbol, op.port_name, op.operation) for op in runnables) == [
        ("PpDiagNew_Clear", "PpDiagNew_Clear", "PpDiagNew", "Clear"),
        ("RunReadDtc", "RunReadDtc", "PpDiagNew", "ReadDtc"),
    ]
    triggers = {op.runnable_name: op for op in outcome.change_set.operations if isinstance(op, AddTrigger)}
    assert set(triggers) == {"PpDiagNew_Clear", "RunReadDtc"}
    assert all(t.kind is TriggerKind.OPERATION_INVOKED for t in triggers.values())
    assert triggers["RunReadDtc"].task == "Task_Event"  # the row names a task
    assert triggers["PpDiagNew_Clear"].task is None  # a new server runnable stays unmapped
    assert triggers["PpDiagNew_Clear"].position_after_event is None
    port_result, trigger_result = outcome.results
    assert len(port_result.operation_ids) == 3  # port, default runnable, its trigger
    assert len(trigger_result.operation_ids) == 2  # named runnable, its trigger


def test_new_server_port_gets_one_runnable_per_operation() -> None:
    outcome = run(port_row("SwcA", "PpDiagNew", "P", "IfDiag2", ""))
    assert statuses(outcome) == (A,)
    assert op_kinds(outcome) == [
        "add_port",
        "add_server_runnable",
        "add_server_runnable",
        "add_trigger",
        "add_trigger",
    ]
    names = [op.name for op in outcome.change_set.operations if isinstance(op, AddServerRunnable)]
    assert names == ["PpDiagNew_ReadDtc", "PpDiagNew_Clear"]


def test_client_port_and_existing_runnable_create_no_runnable() -> None:
    outcome = run(
        port_row("SwcA", "RpDiagNew", "R", "IfDiag", ""),
        port_row("SwcC", "PpDiagNew", "P", "IfDiag", ""),
        trigger_row("SwcC", "RunC_Plain", "on operation invocation", "PpDiagNew", "ReadDtc"),
    )
    assert statuses(outcome) == (A, A, A)
    assert op_kinds(outcome) == ["add_port", "add_port", "add_trigger"]


def test_runnable_with_triggers_in_two_tasks() -> None:
    no_task = run(trigger_row("SwcC", "RunC", "on data reception", "RpMulti", "A"))
    assert statuses(no_task) == (R,)
    assert "Task_10ms, Task_Event" in messages(no_task, c.TRG_TASK_AMBIGUOUS)[0]

    named = run(trigger_row("SwcC", "RunC", "on data reception", "RpMulti", "A", task="Task_10ms"))
    trigger = operation(named, "add_trigger")
    assert isinstance(trigger, AddTrigger)
    assert trigger.task == "Task_10ms"
    assert trigger.position_after_event == f"{behavior('SwcC')}/TMT_RunC_10ms"


def test_new_trigger_takes_the_task_of_the_runnable() -> None:
    outcome = run(
        trigger_row("SwcD", "RunD", "periodic", period_ms="50"),
        trigger_row("SwcD", "RunD", "periodic", period_ms="100", task="Task_Init"),
        trigger_row("SwcC", "RunC_Unmapped", "periodic", period_ms="50"),
        trigger_row("SwcC", "RunC_Plain", "periodic", period_ms="50"),
    )
    assert statuses(outcome) == (A, A, A, A)
    default, named, unmapped, bare = (
        op for op in outcome.change_set.operations if isinstance(op, AddTrigger)
    )
    assert (default.task, default.period_s) == ("Task_10ms", 0.05)
    assert default.position_after_event == f"{behavior('SwcD')}/DRT_RunD_Speed"  # position 5
    assert (named.task, named.position_after_event) == ("Task_Init", None)
    assert (unmapped.task, unmapped.position_after_event) == (None, None)
    assert (bare.task, bare.position_after_event) == (None, None)


def test_task_name_that_does_not_exist() -> None:
    outcome = run(trigger_row("SwcA", "RunA_10ms", "periodic", period_ms="20", task="Task_5ms"))
    assert statuses(outcome) == (R,)
    assert "Task_5ms" in messages(outcome, c.TRG_TASK)[0]


def test_connector_to_a_missing_port_of_a_model_owned_swc_is_held() -> None:
    outcome = run(HELD, MODEL_PORT)
    assert statuses(outcome) == (H, W)
    connector = operation(outcome, "add_connector")
    port = operation(outcome, "add_port")
    assert (connector.route, port.route) == (Route.HOLD, Route.MODEL)
    held, waiting = sorted(outcome.pending.entries, key=lambda e: e.operation.op, reverse=False)
    assert held.state is PendingState.ON_HOLD
    assert held.depends_on == (f"{CT}/SwcM/PpNew",)
    assert waiting.state is PendingState.WAITING_FOR_MODEL_OWNER
    assert waiting.owner_team == "Gamma"
    assert held.created_utc == waiting.created_utc == "2026-11-11T08:00:00Z"
    assert not outcome.change_set.routed(Route.APPLY)


def test_held_connector_is_released_when_the_port_exists() -> None:
    first = run(HELD, MODEL_PORT)
    arrived = extend_model(MODEL, ports=[new_port("SwcM", "PpNew", "P", "IfTorque")])
    second = run(model=arrived, pending=first.pending, now="2026-11-12T08:00:00Z")
    assert {f.code for f in second.findings} == {c.PEND_FULFILLED, c.PEND_RELEASED}
    assert all(f.severity is Severity.INFO for f in second.findings)
    assert not second.pending.entries
    (released,) = second.change_set.operations
    assert isinstance(released, AddConnector)
    assert released.route is Route.APPLY
    assert released.id == operation(first, "add_connector").id
    assert (released.provider_port, released.requester_port) == ("PpNew", "RpTorque")

    # The same workbook imported again: rows and queue describe one request, not two.
    third = run(HELD, MODEL_PORT, model=arrived, pending=first.pending)
    assert statuses(third) == (A, D)
    assert op_kinds(third) == ["add_connector"]
    assert third.results[0].operation_ids == (released.id,)
    assert not third.pending.entries


def test_held_connector_stays_when_the_port_exists_with_another_interface() -> None:
    first = run(HELD, MODEL_PORT)
    wrong = extend_model(MODEL, ports=[new_port("SwcM", "PpNew", "P", "IfSpeed")])
    second = run(model=wrong, pending=first.pending)
    assert {f.code for f in second.findings} == {c.PEND_MISMATCH, c.PEND_RELEASE_FAILED}
    assert not second.change_set.operations
    assert second.pending == first.pending  # nothing is dropped
    assert "IfSpeed" in messages(second, c.PEND_MISMATCH)[0]


def test_pending_entries_stay_while_the_port_is_missing() -> None:
    first = run(HELD, MODEL_PORT)
    second = run(pending=first.pending, now="2026-11-13T08:00:00Z")
    assert not second.findings and not second.change_set.operations
    assert second.pending == first.pending
    late = run(pending=first.pending, now="2026-12-24T08:00:00Z")
    assert [f.code for f in late.findings] == [c.PEND_STALE, c.PEND_STALE]
    assert late.pending == first.pending


def test_stale_baseline_object_renamed_or_removed() -> None:
    removed = extend_model(MODEL, without_ports=[f"{CT}/SwcD/PpUnused"])
    rows = [
        port_row("SwcD", "PpUnused", "P", action="DELETE", rid=port_rid("SwcD", "PpUnused")),
        port_row("SwcD", "PpUnused", "P", init="4", action="MODIFY", rid=port_rid("SwcD", "PpUnused")),
    ]
    outcome = run(*rows[:1], model=removed)
    assert statuses(outcome) == (R,)
    assert "Export a fresh workbook" in messages(outcome, c.ROW_STALE)[0]
    assert statuses(run(rows[1], model=removed)) == (R,)
    assert statuses(run(rows[0])) == (A,)  # against the baseline of the workbook it resolves
    delete = operation(run(rows[0]), "delete_port")
    assert isinstance(delete, DeletePort)
    assert (delete.swc_path, delete.name) == (f"{CT}/SwcD", "PpUnused")


def test_empty_import() -> None:
    outcome = run()
    assert outcome.results == ()
    assert outcome.change_set.operations == ()
    assert outcome.pending == PendingQueue()
    assert outcome.findings == ()
    assert outcome.change_set.created_utc == "2026-11-11T08:00:00Z"
    assert outcome.change_set.baseline_commit == "0123456789abcdef0123456789abcdef01234567"


def test_unknown_action_text() -> None:
    outcome = run(port_row("SwcA", "RpNew", action="UPSERT"), port_row("SwcA", "RpNew2", action="add"))
    assert statuses(outcome) == (R, A)
    assert "UPSERT" in messages(outcome, c.ROW_UNKNOWN_ACTION)[0]


def test_unknown_sheet() -> None:
    outcome = run(raw_row("Unconnected", swc="SwcA", port="RpTorque"), raw_row("Tabelle1"))
    assert statuses(outcome) == (R, R)
    assert codes_of(outcome) == {c.ROW_UNKNOWN_SHEET}


def test_blank_required_cell() -> None:
    outcome = run(port_row("SwcA", "RpNew", direction="", interface=""))
    assert statuses(outcome) == (R,)
    blank = messages(outcome, c.ROW_BLANK_CELL)
    assert len(blank) == 2 and "'Direction'" in blank[0] and "'Interface'" in blank[1]


def test_model_owned_swc_is_validated_then_routed() -> None:
    outcome = run(
        port_row("SwcM", "PpNew", "P", "IfTorque", "0"),
        port_row("SwcM", "PpBad", "PX", "IfTorque", "0"),
        port_row("SwcM", "PpDiagM", "P", "IfDiag", ""),
    )
    assert statuses(outcome) == (W, R, W)
    assert {op.route for op in outcome.change_set.operations} == {Route.MODEL}
    assert op_kinds(outcome) == ["add_port", "add_port", "add_server_runnable", "add_trigger"]
    assert len(outcome.pending.entries) == 4
    assert {entry.owner_team for entry in outcome.pending.entries} == {"Gamma"}
    server = next(e for e in outcome.pending.entries if isinstance(e.operation, AddServerRunnable))
    assert server.depends_on == (f"{CT}/SwcM/PpDiagM",)
    port = operation(outcome, "add_port")
    assert isinstance(port, AddPort)
    assert (port.direction, port.interface_path) == (PortDirection.P, f"{IF}/IfTorque")
    assert port.init_values[0].value == "0"


def test_ten_thousand_requests_in_one_import() -> None:
    rows = []
    for index in range(2500):
        rows.append(port_row("SwcA", f"RpBulk{index}", "R", "IfSpeed", str(index % 100)))
        rows.append(port_row("SwcB", f"PpBulk{index}", "P", "IfSpeed", "0"))
        rows.append(connection_row("SwcB", f"PpBulk{index}", "SwcA", f"RpBulk{index}"))
        rows.append(access_row("SwcA", "RunA_10ms", f"RpBulk{index}", "Speed", "implicit read"))
    started = time.perf_counter()
    outcome = run(*rows)
    elapsed = time.perf_counter() - started
    assert len(outcome.results) == 10_000
    assert all(result.status is RequestStatus.ACCEPTED for result in outcome.results)
    assert len(outcome.change_set.operations) == 10_000
    assert elapsed < 30.0, f"10,000 requests took {elapsed:.1f} s"
