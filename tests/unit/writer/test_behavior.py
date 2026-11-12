"""AddAccessPoint, AddTrigger and AddServerRunnable, verified by reloading with the reader."""

from __future__ import annotations

from typing import Any

from lxml import etree

from porttool.core.models import AccessKind, PortDirection, TriggerKind
from porttool.core.operations import (
    AddAccessPoint,
    AddPort,
    AddServerRunnable,
    AddTrigger,
    AnyOperation,
)
from porttool.writer import codes
from porttool.writer.order import ORDER
from tests.unit.writer.conftest import (
    CT,
    IF,
    Repo,
    added,
    assert_only_insertions,
    codes_of,
    errors,
)

SWC1 = f"{CT}/Swc1"
SWC2 = f"{CT}/Swc2"
B1 = f"{SWC1}/Swc1_InternalBehavior"
B2 = f"{SWC2}/Swc2_InternalBehavior"
SWC1_FILE = "Config/Developer/ComponentTypes/Swc1.arxml"
SWC2_FILE = "Config/Developer/ComponentTypes/Swc2.arxml"


def access(swc: str, runnable: str, port: str, target: str, kind: AccessKind) -> AddAccessPoint:
    return AddAccessPoint(
        id=f"acc-{runnable}-{port}-{target}-{kind.name}",
        swc_path=swc,
        runnable_name=runnable,
        port_name=port,
        target=target,
        kind=kind,
    )


def trigger(swc: str, runnable: str, kind: TriggerKind, **more: Any) -> AddTrigger:
    return AddTrigger(
        id=f"trg-{runnable}-{kind.name}", swc_path=swc, runnable_name=runnable, kind=kind, **more
    )


OPERATIONS: tuple[AnyOperation, ...] = (
    access(SWC2, "Swc2_Cyclic10ms", "RpSig1", "De1", AccessKind.IMPLICIT_READ),
    access(SWC2, "Swc2_Cyclic10ms", "RpSig3", "De1", AccessKind.EXPLICIT_READ),
    access(SWC2, "Swc2_Cyclic10ms", "RpSrv2", "Op1", AccessKind.ASYNC_SERVER_CALL),
    access(SWC1, "Swc1_Cyclic100ms", "PpSig3", "De1", AccessKind.IMPLICIT_WRITE),
    access(SWC1, "Swc1_Cyclic100ms", "PpSig3", "De2", AccessKind.EXPLICIT_SEND),
    access(SWC1, "Swc1_Cyclic10ms", "RpSrv2", "Op1", AccessKind.SYNC_SERVER_CALL),
    trigger(SWC1, "Swc1_Cyclic100ms", TriggerKind.PERIODIC, period_s=0.5, task="Task_500ms"),
    trigger(SWC2, "Swc2_Cyclic10ms", TriggerKind.DATA_RECEIVED, port_name="RpSig3", target="De1"),
    trigger(
        SWC2, "Swc2_Cyclic100ms", TriggerKind.DATA_RECEIVE_ERROR, port_name="RpSig3", target="De2"
    ),
    trigger(SWC2, "Swc2_Cyclic10ms", TriggerKind.INIT),
    # a server port, its runnable, the runnable's trigger and an access: all in one change set
    AddPort(
        id="port-srv",
        swc_path=SWC1,
        name="PpNewSrv",
        direction=PortDirection.P,
        interface_path=f"{IF}/IfSrv2",
    ),
    AddServerRunnable(
        id="run-srv",
        swc_path=SWC1,
        name="PpNewSrv_Op1",
        symbol="Swc1_PpNewSrv_Op1",
        port_name="PpNewSrv",
        operation="Op1",
    ),
    trigger(
        SWC1,
        "PpNewSrv_Op1",
        TriggerKind.OPERATION_INVOKED,
        port_name="PpNewSrv",
        target="Op1",
        position_after_event=f"{B1}/OIT_PpSrv2_Op1",
    ),
    access(SWC1, "PpNewSrv_Op1", "PpSig1", "De1", AccessKind.IMPLICIT_WRITE),
)


def _assert_schema_order(data: bytes) -> None:
    """Children of every element with an order table appear in the order of the table."""
    for el in etree.fromstring(data).iter("{*}*"):
        table = ORDER.get(etree.QName(el).localname)
        if table is None:
            continue
        ranks = [table.index(etree.QName(child).localname) for child in el.iterchildren("{*}*")]
        assert ranks == sorted(ranks), (etree.QName(el).localname, [c.tag for c in el])


def test_fixture_files_follow_the_order_tables(small: Repo) -> None:
    for name, data in small.snapshot().items():
        if name.endswith(".arxml"):
            _assert_schema_order(data)


def test_access_points_triggers_and_server_runnable_after_apply(small: Repo) -> None:
    before_model = small.model
    before = small.snapshot()
    plan = small.plan(*OPERATIONS)
    assert not plan.findings, plan.findings
    assert len(plan.preview) == len(OPERATIONS)
    assert plan.preview[0] == (
        "Swc2: Swc2_Cyclic10ms gets implicit read of RpSig1.De1 (DRA_RpSig1_De1)"
    )
    assert plan.preview[6] == (
        "Swc1: Swc1_Cyclic100ms gets trigger 'periodic' (TMT_Swc1_Cyclic100ms_1), "
        "period 0.5 s, task Task_500ms"
    )
    assert plan.preview[11] == (
        "Swc1: add server runnable PpNewSrv_Op1 (symbol Swc1_PpNewSrv_Op1) for PpNewSrv.Op1"
    )
    outcome = small.apply_plan(plan)
    assert not errors(outcome.findings), outcome.findings
    assert outcome.written == (SWC1_FILE, SWC2_FILE)
    assert set(outcome.applied_operation_ids) == {op.id for op in OPERATIONS}
    after = small.snapshot()
    assert assert_only_insertions(before, after) == {SWC1_FILE, SWC2_FILE}
    _assert_schema_order(after[SWC1_FILE])
    _assert_schema_order(after[SWC2_FILE])

    model = small.reload()
    assert not model.findings, model.findings
    r10, r100 = f"{B2}/Swc2_Cyclic10ms", f"{B1}/Swc1_Cyclic100ms"
    server = f"{B1}/PpNewSrv_Op1"
    assert added(before_model, model) == {
        f"access_points:{r10}/DRA_RpSig1_De1",
        f"access_points:{r10}/DRP_RpSig3_De1",
        f"access_points:{r10}/ASCP_RpSrv2_Op1",
        f"access_points:{r100}/DWA_PpSig3_De1",
        f"access_points:{r100}/DSP_PpSig3_De2",
        f"access_points:{B1}/Swc1_Cyclic10ms/SSCP_RpSrv2_Op1",
        f"access_points:{server}/DWA_PpSig1_De1",
        f"triggers:{B1}/TMT_Swc1_Cyclic100ms_1",
        f"triggers:{B2}/DRT_Swc2_Cyclic10ms_RpSig3_De1",
        f"triggers:{B2}/DRET_Swc2_Cyclic100ms_RpSig3_De2",
        f"triggers:{B2}/IT_Swc2_Cyclic10ms",
        f"triggers:{B1}/OIT_PpNewSrv_Op1_PpNewSrv_Op1",
        f"ports:{SWC1}/PpNewSrv",
        f"runnables:{server}",
    }
    points = model.access_points
    for path, kind, port, target in (
        (f"{r10}/DRA_RpSig1_De1", AccessKind.IMPLICIT_READ, f"{SWC2}/RpSig1", "De1"),
        (f"{r10}/DRP_RpSig3_De1", AccessKind.EXPLICIT_READ, f"{SWC2}/RpSig3", "De1"),
        (f"{r10}/ASCP_RpSrv2_Op1", AccessKind.ASYNC_SERVER_CALL, f"{SWC2}/RpSrv2", "Op1"),
        (f"{r100}/DWA_PpSig3_De1", AccessKind.IMPLICIT_WRITE, f"{SWC1}/PpSig3", "De1"),
        (f"{r100}/DSP_PpSig3_De2", AccessKind.EXPLICIT_SEND, f"{SWC1}/PpSig3", "De2"),
        (
            f"{B1}/Swc1_Cyclic10ms/SSCP_RpSrv2_Op1",
            AccessKind.SYNC_SERVER_CALL,
            f"{SWC1}/RpSrv2",
            "Op1",
        ),
        (f"{server}/DWA_PpSig1_De1", AccessKind.IMPLICIT_WRITE, f"{SWC1}/PpSig1", "De1"),
    ):
        got = points[path]
        assert (got.kind, got.port_path, got.target) == (kind, port, target), path
        assert got.runnable_path == path.rsplit("/", 1)[0]
    triggers = model.triggers
    periodic = triggers[f"{B1}/TMT_Swc1_Cyclic100ms_1"]
    assert (periodic.kind, periodic.runnable_path, periodic.period_s) == (
        TriggerKind.PERIODIC,
        r100,
        0.5,
    )
    received = triggers[f"{B2}/DRT_Swc2_Cyclic10ms_RpSig3_De1"]
    assert (received.kind, received.runnable_path, received.port_path, received.target) == (
        TriggerKind.DATA_RECEIVED,
        r10,
        f"{SWC2}/RpSig3",
        "De1",
    )
    failed = triggers[f"{B2}/DRET_Swc2_Cyclic100ms_RpSig3_De2"]
    assert (failed.kind, failed.port_path, failed.target) == (
        TriggerKind.DATA_RECEIVE_ERROR,
        f"{SWC2}/RpSig3",
        "De2",
    )
    init = triggers[f"{B2}/IT_Swc2_Cyclic10ms"]
    assert (init.kind, init.runnable_path, init.port_path) == (TriggerKind.INIT, r10, None)
    invoked = triggers[f"{B1}/OIT_PpNewSrv_Op1_PpNewSrv_Op1"]
    assert (invoked.kind, invoked.runnable_path, invoked.port_path, invoked.target) == (
        TriggerKind.OPERATION_INVOKED,
        server,
        f"{SWC1}/PpNewSrv",
        "Op1",
    )
    runnable = model.runnables[server]
    assert (
        runnable.symbol,
        runnable.can_be_invoked_concurrently,
        runnable.min_start_interval_s,
    ) == (
        "Swc1_PpNewSrv_Op1",
        False,
        0.0,
    )


def test_created_events_are_reported_with_path_and_requested_task(small: Repo) -> None:
    outcome = small.apply(*OPERATIONS)
    events = [f for f in outcome.findings if f.code == codes.INFO_EVENT]
    assert [f.object_path for f in events] == [
        f"{B1}/TMT_Swc1_Cyclic100ms_1",
        f"{B2}/DRT_Swc2_Cyclic10ms_RpSig3_De1",
        f"{B2}/DRET_Swc2_Cyclic100ms_RpSig3_De2",
        f"{B2}/IT_Swc2_Cyclic10ms",
        f"{B1}/OIT_PpNewSrv_Op1_PpNewSrv_Op1",
    ]
    assert events[0].message == (
        f"[op trg-Swc1_Cyclic100ms-PERIODIC] Created TIMING-EVENT {B1}/TMT_Swc1_Cyclic100ms_1 "
        "for runnable Swc1_Cyclic100ms; requested task: Task_500ms; after event: none."
    )
    assert events[0].file == SWC1_FILE
    assert events[4].message.endswith(f"requested task: none; after event: {B1}/OIT_PpSrv2_Op1.")
    # the dry run reports problems only
    small.reload()
    again = small.plan(trigger(SWC2, "Swc2_Cyclic100ms", TriggerKind.INIT))
    assert not again.findings and len(again.preview) == 1


def test_name_patterns_are_settings(small: Repo) -> None:
    small.setting(
        access_point_pattern="{runnable}_{prefix}_{port}_{target}",
        access_point_prefixes={"implicit read": "Read"},
        event_patterns={"init": "Init_{runnable}"},
    )
    outcome = small.apply(
        access(SWC2, "Swc2_Cyclic10ms", "RpSig1", "De1", AccessKind.IMPLICIT_READ),
        trigger(SWC2, "Swc2_Cyclic10ms", TriggerKind.INIT),
    )
    assert not errors(outcome.findings)
    model = small.reload()
    assert f"{B2}/Swc2_Cyclic10ms/Swc2_Cyclic10ms_Read_RpSig1_De1" in model.access_points
    assert f"{B2}/Init_Swc2_Cyclic10ms" in model.triggers


def test_wrong_name_pattern_gives_a_warning_and_the_default(small: Repo) -> None:
    small.setting(access_point_pattern="{nope}", event_patterns={"init": "{"}, queue_length=-1)
    plan = small.plan(trigger(SWC2, "Swc2_Cyclic10ms", TriggerKind.INIT))
    assert codes_of(plan.findings) == [codes.SETTING] * 3
    assert plan.preview == ("Swc2: Swc2_Cyclic10ms gets trigger 'init' (IT_Swc2_Cyclic10ms)",)


def test_requests_that_do_not_fit_are_findings(small: Repo) -> None:
    plan = small.plan(
        access(SWC2, "Swc2_Cyclic10ms", "RpSig1", "De1", AccessKind.IMPLICIT_WRITE),
        access(SWC2, "Swc2_Cyclic10ms", "RpSig1", "Nope", AccessKind.IMPLICIT_READ),
        access(SWC2, "Swc2_Cyclic10ms", "RpSig1", "De1", AccessKind.SYNC_SERVER_CALL),
        access(SWC2, "Swc2_Cyclic10ms", "RpSig1", "De1", AccessKind.QUEUED_RECEIVE),
        access(SWC2, "Swc2_Cyclic10ms", "RpSig1", "De1", AccessKind.MODE_ACCESS),
        access(SWC2, "NoRunnable", "RpSig1", "De1", AccessKind.IMPLICIT_READ),
        trigger(SWC2, "Swc2_Cyclic10ms", TriggerKind.PERIODIC),
        trigger(SWC2, "Swc2_Cyclic10ms", TriggerKind.DATA_RECEIVED, port_name="RpSig1"),
        trigger(
            SWC2, "Swc2_Cyclic10ms", TriggerKind.OPERATION_INVOKED, port_name="RpSrv2", target="Op1"
        ),
        trigger(SWC2, "Swc2_Cyclic10ms", TriggerKind.MODE_SWITCH, port_name="RpSig1", target="X"),
        AddServerRunnable(
            id="run",
            swc_path=SWC2,
            name="RpSrv2_Op1",
            symbol="x",
            port_name="RpSrv2",
            operation="Op1",
        ),
    )
    # findings come in apply order: server runnable, access points, triggers
    assert codes_of(plan.findings) == [
        codes.REF_KIND,
        codes.REF_KIND,
        codes.REF_MISSING,
        codes.REF_KIND,
        codes.REF_KIND,
        codes.UNSUPPORTED,
        codes.REF_MISSING,
        codes.INCOMPLETE,
        codes.INCOMPLETE,
        codes.REF_KIND,
        codes.UNSUPPORTED,
    ]
    mode = next(f for f in plan.findings if "on mode switch" in f.message)
    assert mode.code == codes.UNSUPPORTED and "not supported yet" in mode.message
    assert not plan.files and not plan.preview
