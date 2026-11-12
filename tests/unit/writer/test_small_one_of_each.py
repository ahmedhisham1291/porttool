"""The required verification: one of each Add operation on the SMALL generated repo.

Load with the reader, apply, reload: the new model holds exactly the requested objects
with the right attributes, every old object is unchanged, the reader has no new
finding, and every modified file only gained bytes.
"""

from __future__ import annotations

from porttool.core.models import (
    AccessKind,
    ArgumentDirection,
    ComSpec,
    ConnectorEnd,
    DataElement,
    DataTypeCategory,
    DataTypeLevel,
    InterfaceKind,
    PortDirection,
    TriggerKind,
)
from porttool.core.operations import (
    AddAccessPoint,
    AddConnector,
    AddDataType,
    AddInterface,
    AddPort,
    AddServerRunnable,
    AddTrigger,
    AnyOperation,
    ArgumentSpec,
    ChangeSet,
    ElementSpec,
    InitValueSpec,
    OperationSpec,
)
from tests.unit.writer.conftest import (
    COMPOSITION,
    CT,
    DT,
    IF,
    Repo,
    added,
    assert_only_insertions,
)

SWC1, SWC2 = f"{CT}/Swc1", f"{CT}/Swc2"
B1, B2 = f"{SWC1}/Swc1_InternalBehavior", f"{SWC2}/Swc2_InternalBehavior"

#: Deliberately not in apply order: the writer sorts. Every later operation uses an
#: object that an earlier kind creates in the same change set.
ONE_OF_EACH: tuple[AnyOperation, ...] = (
    AddConnector(
        id="connector",
        composition_path=COMPOSITION,
        name="Swc1_PpNew_Swc2_RpNew",
        provider_prototype="Swc1",
        provider_port="PpNew",
        requester_prototype="Swc2",
        requester_port="RpNew",
    ),
    AddTrigger(
        id="trigger-invoked",
        swc_path=SWC1,
        runnable_name="PpSvc_Get",
        kind=TriggerKind.OPERATION_INVOKED,
        port_name="PpSvc",
        target="Get",
    ),
    AddTrigger(
        id="trigger-received",
        swc_path=SWC2,
        runnable_name="Swc2_OnRpSig1",
        kind=TriggerKind.DATA_RECEIVED,
        port_name="RpNew",
        target="Plain",
        task="Task_Event",
    ),
    AddAccessPoint(
        id="access-read",
        swc_path=SWC2,
        runnable_name="Swc2_OnRpSig1",
        port_name="RpNew",
        target="Plain",
        kind=AccessKind.IMPLICIT_READ,
    ),
    AddAccessPoint(
        id="access-queue",
        swc_path=SWC2,
        runnable_name="Swc2_OnRpSig1",
        port_name="RpNew",
        target="Queue",
        kind=AccessKind.QUEUED_RECEIVE,
    ),
    AddAccessPoint(
        id="access-send",
        swc_path=SWC1,
        runnable_name="PpSvc_Get",
        port_name="PpNew",
        target="Queue",
        kind=AccessKind.EXPLICIT_SEND,
    ),
    AddServerRunnable(
        id="runnable",
        swc_path=SWC1,
        name="PpSvc_Get",
        symbol="Swc1_PpSvc_Get",
        port_name="PpSvc",
        operation="Get",
    ),
    AddPort(
        id="port-p",
        swc_path=SWC1,
        name="PpNew",
        direction=PortDirection.P,
        interface_path=f"{IF}/IfNew",
        init_values=(InitValueSpec(element="Plain", value="5"),),
    ),
    AddPort(
        id="port-r",
        swc_path=SWC2,
        name="RpNew",
        direction=PortDirection.R,
        interface_path=f"{IF}/IfNew",
        init_values=(
            InitValueSpec(element="Plain", value="5"),
            InitValueSpec(element="Queue", value=None),
        ),
    ),
    AddPort(
        id="port-server",
        swc_path=SWC1,
        name="PpSvc",
        direction=PortDirection.P,
        interface_path=f"{IF}/IfSvc",
    ),
    AddInterface(
        id="interface-sr",
        package_path=IF,
        name="IfNew",
        kind=InterfaceKind.SENDER_RECEIVER,
        elements=(
            ElementSpec(name="Plain", type_path=f"{DT}/T_New"),
            ElementSpec(name="Queue", type_path=f"{DT}/T_New", is_queued=True),
        ),
    ),
    AddInterface(
        id="interface-cs",
        package_path=IF,
        name="IfSvc",
        kind=InterfaceKind.CLIENT_SERVER,
        operations=(
            OperationSpec(
                name="Get",
                arguments=(
                    ArgumentSpec(
                        name="Value", direction=ArgumentDirection.OUT, type_path=f"{DT}/T_New"
                    ),
                ),
            ),
        ),
    ),
    AddDataType(
        id="data-type",
        package_path=DT,
        name="T_New",
        level=DataTypeLevel.IMPLEMENTATION,
        category=DataTypeCategory.VALUE,
        base_type_path=f"{DT}/BaseTypes/uint16",
        lower_limit=0,
        upper_limit=1000,
    ),
)


def test_one_of_each_add_operation_on_the_small_repo(small: Repo) -> None:
    before_model = small.model
    before = small.snapshot()
    assert not before_model.findings
    change_set = ChangeSet.from_json(ChangeSet(operations=ONE_OF_EACH).to_json())
    plan = small.writer.plan(before_model, small.config, change_set)
    assert not plan.findings, plan.findings
    assert len(plan.preview) == len(ONE_OF_EACH)
    assert small.snapshot() == before

    outcome = small.apply_plan(plan)
    assert not [f for f in outcome.findings if f.severity.value != "info"], outcome.findings
    assert set(outcome.applied_operation_ids) == {op.id for op in ONE_OF_EACH}
    after = small.snapshot()
    changed = assert_only_insertions(before, after)
    assert (
        changed
        == set(outcome.written)
        == set(small.journal.paths)
        == {
            "Config/Developer/ComponentTypes/EcuComposition.arxml",
            "Config/Developer/ComponentTypes/Swc1.arxml",
            "Config/Developer/ComponentTypes/Swc2.arxml",
            "Config/Developer/DataTypes/DataTypes.arxml",
            "Config/Developer/PortInterfaces/PortInterfaces.arxml",
        }
    )

    model = small.reload()
    assert not model.findings, model.findings
    assert added(before_model, model) == {
        f"data_types:{DT}/T_New",
        f"constraints:{DT}/DataConstrs/DC_T_New",
        f"interfaces:{IF}/IfNew",
        f"interfaces:{IF}/IfSvc",
        f"ports:{SWC1}/PpNew",
        f"ports:{SWC2}/RpNew",
        f"ports:{SWC1}/PpSvc",
        f"runnables:{B1}/PpSvc_Get",
        f"access_points:{B2}/Swc2_OnRpSig1/DRA_RpNew_Plain",
        f"access_points:{B2}/Swc2_OnRpSig1/DRP_RpNew_Queue",
        f"access_points:{B1}/PpSvc_Get/DSP_PpNew_Queue",
        f"triggers:{B1}/OIT_PpSvc_Get_PpSvc_Get",
        f"triggers:{B2}/DRT_Swc2_OnRpSig1_RpNew_Plain",
        f"connectors:{COMPOSITION}/Swc1_PpNew_Swc2_RpNew",
    }

    new_type = model.data_types[f"{DT}/T_New"]
    assert (new_type.level, new_type.category, new_type.base_type_path) == (
        DataTypeLevel.IMPLEMENTATION,
        DataTypeCategory.VALUE,
        f"{DT}/BaseTypes/uint16",
    )
    assert new_type.constraint_path == f"{DT}/DataConstrs/DC_T_New"
    constraint = model.constraints[f"{DT}/DataConstrs/DC_T_New"]
    assert (constraint.lower, constraint.upper) == (0.0, 1000.0)

    interface = model.interfaces[f"{IF}/IfNew"]
    assert interface.kind is InterfaceKind.SENDER_RECEIVER
    assert interface.elements == (
        DataElement("Plain", f"{DT}/T_New", False),
        DataElement("Queue", f"{DT}/T_New", True),
    )
    service = model.interfaces[f"{IF}/IfSvc"]
    assert service.kind is InterfaceKind.CLIENT_SERVER
    assert [
        (o.name, [(a.name, a.direction, a.type_path) for a in o.arguments])
        for o in service.operations
    ] == [("Get", [("Value", ArgumentDirection.OUT, f"{DT}/T_New")])]

    sender = model.ports[f"{SWC1}/PpNew"]
    assert (sender.direction, sender.interface_path) == (PortDirection.P, f"{IF}/IfNew")
    assert sender.com_specs == (ComSpec("Plain", "5"), ComSpec("Queue", None))
    receiver = model.ports[f"{SWC2}/RpNew"]
    assert (receiver.direction, receiver.interface_path) == (PortDirection.R, f"{IF}/IfNew")
    assert receiver.com_specs == (ComSpec("Plain", "5"), ComSpec("Queue", None, 1))
    server = model.ports[f"{SWC1}/PpSvc"]
    assert (server.direction, server.interface_path) == (PortDirection.P, f"{IF}/IfSvc")
    assert server.com_specs == (ComSpec("Get", None, 1),)

    runnable = model.runnables[f"{B1}/PpSvc_Get"]
    assert (runnable.swc_path, runnable.symbol) == (SWC1, "Swc1_PpSvc_Get")
    read = model.access_points[f"{B2}/Swc2_OnRpSig1/DRA_RpNew_Plain"]
    assert (read.kind, read.port_path, read.target) == (
        AccessKind.IMPLICIT_READ,
        f"{SWC2}/RpNew",
        "Plain",
    )
    queued = model.access_points[f"{B2}/Swc2_OnRpSig1/DRP_RpNew_Queue"]
    assert (queued.kind, queued.port_path, queued.target) == (
        AccessKind.QUEUED_RECEIVE,
        f"{SWC2}/RpNew",
        "Queue",
    )
    send = model.access_points[f"{B1}/PpSvc_Get/DSP_PpNew_Queue"]
    assert (send.kind, send.runnable_path, send.port_path, send.target) == (
        AccessKind.EXPLICIT_SEND,
        f"{B1}/PpSvc_Get",
        f"{SWC1}/PpNew",
        "Queue",
    )
    invoked = model.triggers[f"{B1}/OIT_PpSvc_Get_PpSvc_Get"]
    assert (invoked.kind, invoked.runnable_path, invoked.port_path, invoked.target) == (
        TriggerKind.OPERATION_INVOKED,
        f"{B1}/PpSvc_Get",
        f"{SWC1}/PpSvc",
        "Get",
    )
    received = model.triggers[f"{B2}/DRT_Swc2_OnRpSig1_RpNew_Plain"]
    assert (received.kind, received.runnable_path, received.port_path, received.target) == (
        TriggerKind.DATA_RECEIVED,
        f"{B2}/Swc2_OnRpSig1",
        f"{SWC2}/RpNew",
        "Plain",
    )
    connector = model.connectors[f"{COMPOSITION}/Swc1_PpNew_Swc2_RpNew"]
    assert connector.provider == ConnectorEnd(f"{COMPOSITION}/Swc1", f"{SWC1}/PpNew")
    assert connector.requester == ConnectorEnd(f"{COMPOSITION}/Swc2", f"{SWC2}/RpNew")
    # the two connected ports are connected, the new server port is not
    unconnected = {instance.key for instance in model.unconnected()}
    old_unconnected = {instance.key for instance in before_model.unconnected()}
    assert unconnected - old_unconnected == {(f"{COMPOSITION}/Swc1", f"{SWC1}/PpSvc")}
    # no task mapping was touched: that is the pipeline's job through DaVinci Configurator
    assert model.task_mappings == before_model.task_mappings
