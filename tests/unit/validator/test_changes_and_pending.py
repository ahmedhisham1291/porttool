"""MODIFY/DELETE target lookup (RowID first, names second) and the pending queue review."""

from __future__ import annotations

import pytest

from porttool.core.models import (
    AccessKind,
    DataTypeCategory,
    DataTypeLevel,
    InterfaceKind,
    TriggerKind,
)
from porttool.core.operations import (
    AddAccessPoint,
    AddDataType,
    AddInterface,
    AddServerRunnable,
    AddTrigger,
    AnyOperation,
    DeleteAccessPoint,
    DeleteConnector,
    DeleteTrigger,
    InitValueSpec,
    ModifyPort,
    Route,
)
from porttool.core.results import RawRequestRow
from porttool.validator import codes as c
from tests.helpers.sample_model import COMPOSITION, CT, IF
from tests.unit.validator.support import (
    A,
    R,
    W,
    access_rid,
    access_row,
    codes_of,
    connection_row,
    connector_rid,
    op_kinds,
    operation,
    port_rid,
    port_row,
    queue_of,
    run,
    statuses,
    trigger_rid,
    trigger_row,
    waiting_entry,
)

CONNECTOR = "SwcA_PpSpeed_SwcB_RpSpeed"
CN = connector_rid(CONNECTOR)
RA = access_rid("SwcC", "RunC", "DRA_RpMulti_A")
RT = trigger_rid("SwcC", "TMT_RunC_Unmapped")
STALE = "XX-000000000000"


def _delete_connection(
    *cells: str, rid: str = CN, composition: str = "", name: str = ""
) -> RawRequestRow:
    padded = (*cells, "", "", "", "")[:4]
    return connection_row(*padded, composition=composition, name=name, action="DELETE", rid=rid)


LOOKUPS = [
    ("connector: RowID alone", _delete_connection(), A, None),
    (
        "connector: composition and name",
        _delete_connection(composition="EcuComposition", name=CONNECTOR),
        A,
        None,
    ),
    ("connector: end points", _delete_connection("SwcA", "PpSpeed", "SwcB", "RpSpeed"), A, None),
    ("connector: renamed", _delete_connection(name="Renamed"), R, c.ROW_IDENTITY_CHANGED),
    (
        "connector: other end point",
        _delete_connection("SwcA", "PpSpeed", "SwcC", "RpFree"),
        R,
        c.ROW_IDENTITY_CHANGED,
    ),
    (
        "connector: outdated RowID, names match",
        _delete_connection("SwcA", "PpSpeed", "SwcB", "RpSpeed", rid=STALE),
        A,
        c.ROW_STALE_ROWID,
    ),
    (
        "connector: gone",
        _delete_connection("SwcA", "PpSpeed", "SwcC", "RpFree", rid=STALE),
        R,
        c.ROW_STALE,
    ),
    ("access: RowID alone", access_row("", "", "", "", "", action="DELETE", rid=RA), A, None),
    (
        "access: names and RowID",
        access_row("SwcC", "RunC", "RpMulti", "A", "implicit read", action="DELETE", rid=RA),
        A,
        None,
    ),
    (
        "access: kind changed",
        access_row("SwcC", "RunC", "RpMulti", "A", "explicit read", action="DELETE", rid=RA),
        R,
        c.ROW_IDENTITY_CHANGED,
    ),
    (
        "access: outdated RowID, names match",
        access_row("SwcC", "RunC", "RpMulti", "A", "implicit read", action="DELETE", rid=STALE),
        A,
        c.ROW_STALE_ROWID,
    ),
    (
        "access: gone",
        access_row("SwcC", "RunC", "RpMulti", "B", "implicit read", action="DELETE", rid=STALE),
        R,
        c.ROW_STALE,
    ),
    ("trigger: RowID alone", trigger_row("", "", "", action="DELETE", rid=RT), A, None),
    (
        "trigger: names and RowID",
        trigger_row("SwcC", "RunC_Unmapped", "periodic", period_ms="100", action="DELETE", rid=RT),
        A,
        None,
    ),
    (
        "trigger: runnable changed",
        trigger_row("SwcC", "RunC", "periodic", period_ms="100", action="DELETE", rid=RT),
        R,
        c.ROW_IDENTITY_CHANGED,
    ),
    (
        "trigger: kind changed",
        trigger_row("SwcC", "RunC_Unmapped", "init", action="DELETE", rid=RT),
        R,
        c.ROW_IDENTITY_CHANGED,
    ),
    (
        "trigger: outdated RowID, names match",
        trigger_row(
            "SwcC", "RunC_Unmapped", "periodic", period_ms="100", action="DELETE", rid=STALE
        ),
        A,
        c.ROW_STALE_ROWID,
    ),
    (
        "trigger: gone",
        trigger_row(
            "SwcC", "RunC_Unmapped", "periodic", period_ms="250", action="DELETE", rid=STALE
        ),
        R,
        c.ROW_STALE,
    ),
    (
        "port: RowID alone",
        port_row("", "", "", "", "", action="DELETE", rid=port_rid("SwcD", "PpUnused")),
        A,
        None,
    ),
    (
        "port: SWC by absolute path",
        port_row(f"{CT}/SwcD", "PpUnused", "P", action="DELETE", rid=port_rid("SwcD", "PpUnused")),
        A,
        None,
    ),
    (
        "port: other SWC in the row",
        port_row("SwcC", "PpUnused", "P", action="DELETE", rid=port_rid("SwcD", "PpUnused")),
        R,
        c.ROW_IDENTITY_CHANGED,
    ),
]


@pytest.mark.parametrize(
    ("row", "status", "code"), [x[1:] for x in LOOKUPS], ids=[x[0] for x in LOOKUPS]
)
def test_delete_target_lookup(row: RawRequestRow, status: object, code: str | None) -> None:
    outcome = run(row)
    assert statuses(outcome) == (status,)
    assert codes_of(outcome) == ({code} if code else set())
    assert len(outcome.change_set.operations) == (1 if status is A else 0)


def test_delete_operations_name_the_object_of_the_row() -> None:
    outcome = run(
        _delete_connection(),
        access_row("", "", "", "", "", action="DELETE", rid=RA),
        trigger_row("", "", "", action="DELETE", rid=RT),
    )
    assert op_kinds(outcome) == ["delete_connector", "delete_trigger", "delete_access_point"]
    connector = operation(outcome, "delete_connector")
    assert isinstance(connector, DeleteConnector)
    assert (connector.composition_path, connector.name) == (COMPOSITION, CONNECTOR)
    trigger = operation(outcome, "delete_trigger")
    assert isinstance(trigger, DeleteTrigger)
    assert (trigger.swc_path, trigger.name) == (f"{CT}/SwcC", "TMT_RunC_Unmapped")
    access = operation(outcome, "delete_access_point")
    assert isinstance(access, DeleteAccessPoint)
    assert (access.runnable_name, access.port_name, access.target, access.kind) == (
        "RunC",
        "RpMulti",
        "A",
        AccessKind.IMPLICIT_READ,
    )


def test_deletes_on_a_model_owned_swc_wait_for_the_owner() -> None:
    outcome = run(
        trigger_row("", "", "", action="DELETE", rid=trigger_rid("SwcM", "TMT_RunM_10ms")),
        port_row("SwcM", "RpSpeedM", action="DELETE", rid=port_rid("SwcM", "RpSpeedM")),
    )
    assert statuses(outcome) == (W, W)
    assert {entry.owner_team for entry in outcome.pending.entries} == {"Gamma"}


_M = f"{CT}/SwcM"
_C = f"{CT}/SwcC"
_B = f"{CT}/SwcB"


def _waiting(op: AnyOperation) -> AnyOperation:
    return op.model_copy(update={"route": Route.MODEL, "requester": "bob"})


FULFILLED: list[AnyOperation] = [
    ModifyPort(
        id="m1",
        swc_path=_M,
        name="PpTorque",
        init_values=(InitValueSpec(element="Torque", value="0"),),
    ),
    ModifyPort(id="m2", swc_path=_M, name="PpTorque", interface_path=f"{IF}/IfTorque"),
    DeleteAccessPoint(
        id="a1",
        swc_path=_M,
        runnable_name="RunM",
        port_name="RpSpeedM",
        target="Speed",
        kind=AccessKind.IMPLICIT_READ,
    ),
    DeleteTrigger(id="t1", swc_path=_M, name="TMT_Gone"),
    AddAccessPoint(
        id="a2",
        swc_path=_C,
        runnable_name="RunC",
        port_name="RpMulti",
        target="A",
        kind=AccessKind.IMPLICIT_READ,
    ),
    AddServerRunnable(
        id="s1",
        swc_path=_B,
        name="Other_Name",
        symbol="Other_Name",
        port_name="PpDiag",
        operation="ReadDtc",
    ),
    AddTrigger(
        id="t2",
        swc_path=_B,
        runnable_name="Other_Name",
        kind=TriggerKind.OPERATION_INVOKED,
        port_name="PpDiag",
        target="ReadDtc",
    ),
    AddTrigger(
        id="t3",
        swc_path=_B,
        runnable_name="RunB_OnSpeed",
        kind=TriggerKind.DATA_RECEIVED,
        port_name="RpSpeed",
        target="Speed",
    ),
    DeleteConnector(id="c1", composition_path=COMPOSITION, name="Gone_Connector"),
    AddDataType(
        id="d1",
        package_path="/DataTypes",
        name="uint8",
        level=DataTypeLevel.IMPLEMENTATION,
        category=DataTypeCategory.VALUE,
    ),
    AddInterface(
        id="i1", package_path="/PortInterfaces", name="IfSpeed", kind=InterfaceKind.SENDER_RECEIVER
    ),
]

WAITING: list[AnyOperation] = [
    ModifyPort(
        id="m1",
        swc_path=_M,
        name="PpTorque",
        init_values=(InitValueSpec(element="Torque", value="9"),),
    ),
    ModifyPort(id="m2", swc_path=_M, name="PpTorque", interface_path=f"{IF}/IfSpeed"),
    DeleteAccessPoint(
        id="a1",
        swc_path=_C,
        runnable_name="RunC",
        port_name="RpMulti",
        target="A",
        kind=AccessKind.IMPLICIT_READ,
    ),
    DeleteTrigger(id="t1", swc_path=_M, name="TMT_RunM_10ms"),
    AddAccessPoint(
        id="a2",
        swc_path=_M,
        runnable_name="RunM",
        port_name="RpSpeedM",
        target="Speed",
        kind=AccessKind.IMPLICIT_READ,
    ),
    AddAccessPoint(
        id="a3",
        swc_path=_M,
        runnable_name="RunNope",
        port_name="RpSpeedM",
        target="Speed",
        kind=AccessKind.IMPLICIT_READ,
    ),
    AddServerRunnable(
        id="s1",
        swc_path=_C,
        name="PpDiagAlt_ReadDtc",
        symbol="x",
        port_name="PpDiagAlt",
        operation="ReadDtc",
    ),
    AddTrigger(
        id="t2",
        swc_path=_C,
        runnable_name="x",
        kind=TriggerKind.OPERATION_INVOKED,
        port_name="PpDiagAlt",
        target="ReadDtc",
    ),
    AddTrigger(id="t3", swc_path=_M, runnable_name="RunM", kind=TriggerKind.PERIODIC, period_s=0.5),
    AddTrigger(id="t4", swc_path=_M, runnable_name="RunNope", kind=TriggerKind.INIT),
    DeleteConnector(id="c1", composition_path=COMPOSITION, name=CONNECTOR),
    AddDataType(
        id="d1",
        package_path="/DataTypes",
        name="NotYet",
        level=DataTypeLevel.IMPLEMENTATION,
        category=DataTypeCategory.VALUE,
    ),
]


@pytest.mark.parametrize("op", FULFILLED, ids=[op.id + ":" + op.op for op in FULFILLED])
def test_pending_entry_is_closed_when_the_repo_fulfils_it(op: AnyOperation) -> None:
    outcome = run(pending=queue_of(waiting_entry(_waiting(op))))
    assert [finding.code for finding in outcome.findings] == [c.PEND_FULFILLED]
    assert not outcome.pending.entries
    assert not outcome.change_set.operations


@pytest.mark.parametrize("op", WAITING, ids=[op.id + ":" + op.op for op in WAITING])
def test_pending_entry_stays_while_the_repo_does_not_fulfil_it(op: AnyOperation) -> None:
    queue = queue_of(waiting_entry(_waiting(op), created="2026-11-10T08:00:00+00:00"))
    outcome = run(pending=queue)
    assert not outcome.findings
    assert outcome.pending == queue


def test_server_runnable_named_by_the_model_owner_closes_the_entry() -> None:
    outcome = run(pending=queue_of(waiting_entry(_waiting(FULFILLED[5]))))
    assert "PpDiag_ReadDtc" in outcome.findings[0].message


def test_pending_modify_of_a_vanished_port_is_reported_and_kept() -> None:
    op = ModifyPort(id="m", swc_path=_M, name="PpVanished", interface_path=f"{IF}/IfSpeed")
    queue = queue_of(waiting_entry(_waiting(op)))
    outcome = run(pending=queue)
    assert [finding.code for finding in outcome.findings] == [c.PEND_ORPHAN]
    assert outcome.pending == queue


def test_age_is_not_judged_without_a_readable_clock() -> None:
    queue = queue_of(waiting_entry(_waiting(WAITING[0]), created="2020-01-01T00:00:00Z"))
    assert [f.code for f in run(pending=queue).findings] == [c.PEND_STALE]
    assert not run(pending=queue, now="not a time").findings
    naive = queue_of(waiting_entry(_waiting(WAITING[0]), created="2026-11-10T08:00:00"))
    assert not run(pending=naive).findings


def test_released_connector_and_a_row_with_another_name_conflict() -> None:
    from tests.unit.validator.rule_cases import _pend_connector

    queue = queue_of(_pend_connector("SwcM", "PpTorque", "SwcA", "RpTorque"))
    same = run(connection_row("SwcM", "PpTorque", "SwcA", "RpTorque"), pending=queue)
    assert statuses(same) == (A,)
    assert op_kinds(same) == ["add_connector"] and not same.pending.entries
    other = run(
        connection_row("SwcM", "PpTorque", "SwcA", "RpTorque", name="Asc_Other"), pending=queue
    )
    assert statuses(other) == (R,)
    assert c.CFL_PENDING in codes_of(other)
    assert op_kinds(other) == ["add_connector"]  # the released one still goes ahead


def test_released_connector_reports_n_to_one_and_counts_for_later_rows() -> None:
    from tests.unit.validator.rule_cases import _pend_connector

    queue = queue_of(_pend_connector("SwcD", "PpSpeedD", "SwcB", "RpSpeed"))
    outcome = run(connection_row("SwcD", "PpSpeedD", "SwcC", "RpFree"), pending=queue)
    assert {f.code for f in outcome.findings} == {c.PEND_RELEASED, c.MUL_N_TO_ONE}
    assert statuses(outcome) == (R,)  # with the released connector this row would make n:m
    assert c.MUL_N_TO_M in codes_of(outcome)


def test_held_connector_is_not_released_onto_a_port_that_a_row_deletes() -> None:
    from tests.unit.validator.rule_cases import _pend_connector

    queue = queue_of(_pend_connector("SwcD", "PpUnused", "SwcC", "RpFree"))
    outcome = run(
        port_row("SwcD", "PpUnused", "P", action="DELETE", rid=port_rid("SwcD", "PpUnused")),
        pending=queue,
    )
    assert statuses(outcome) == (A,)
    assert [f.code for f in outcome.findings] == [c.PEND_RELEASE_FAILED]
    assert outcome.pending == queue
    assert op_kinds(outcome) == ["delete_port"]
