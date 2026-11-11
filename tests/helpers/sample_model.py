"""A small hand-built Model for unit tests of workbook, validator and GUI.

Layout:
    /ComponentTypes/SwcA   application SWC (DaVinci-owned), team Alpha
        PpSpeed    P  IfSpeed (sender-receiver, element Speed: uint16)      connected to SwcB
        RpTorque   R  IfTorque (sender-receiver, element Torque: uint16)    UNCONNECTED
        RpDiag     R  IfDiag (client-server, operation ReadDtc)             connected to SwcB
        runnable RunA_10ms  periodic 10 ms, task Task_10ms
                            implicit write PpSpeed.Speed, sync server call RpDiag.ReadDtc
    /ComponentTypes/SwcB   application SWC (DaVinci-owned), team Beta
        RpSpeed    R  IfSpeed                                               connected to SwcA
        PpDiag     P  IfDiag (server)                                       connected to SwcA
        runnable RunB_OnSpeed  on data reception RpSpeed.Speed, task Task_Event
                               explicit read RpSpeed.Speed
        runnable PpDiag_ReadDtc  on operation invocation PpDiag.ReadDtc, unmapped
    /ComponentTypes/SwcM   application SWC, MODEL-owned (Simulink), team Gamma
        PpTorque   P  IfTorque                                              UNCONNECTED
    /ComponentTypes/EcuComposition  composition with prototypes SwcA, SwcB, SwcM
"""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

from porttool.core.config import OwnerEntry, ProjectConfig
from porttool.core.models import (
    AccessKind,
    AccessPoint,
    Argument,
    ArgumentDirection,
    ArxmlOwner,
    BaseType,
    Component,
    ComponentKind,
    ComponentPrototype,
    Composition,
    ComSpec,
    Connector,
    ConnectorEnd,
    ConnectorKind,
    DataElement,
    DataType,
    DataTypeCategory,
    DataTypeLevel,
    InterfaceKind,
    Model,
    Operation,
    Origin,
    Port,
    PortDirection,
    PortInterface,
    Runnable,
    SourceFile,
    TaskMapping,
    Trigger,
    TriggerKind,
)

CT = "/ComponentTypes"
IF = "/PortInterfaces"
DT = "/DataTypes"
COMPOSITION = f"{CT}/EcuComposition"


def build_sample_model() -> Model:
    """Return the sample model described in the module docstring."""
    file_a = Origin(file="Config/Developer/ComponentTypes/SwcA.arxml")
    file_b = Origin(file="Config/Developer/ComponentTypes/SwcB.arxml")
    file_m = Origin(file="Config/Developer/ComponentTypes/SwcM.arxml")
    file_c = Origin(file="Config/Developer/ComponentTypes/EcuComposition.arxml")
    file_if = Origin(file="Config/Developer/PortInterfaces/Interfaces.arxml")
    file_dt = Origin(file="Config/Developer/DataTypes/DataTypes.arxml")

    base_types = [
        BaseType(path=f"{DT}/BaseTypes/uint16", name="uint16", size_bits=16, origin=file_dt)
    ]
    data_types = [
        DataType(
            path=f"{DT}/uint16",
            name="uint16",
            level=DataTypeLevel.IMPLEMENTATION,
            category=DataTypeCategory.VALUE,
            base_type_path=f"{DT}/BaseTypes/uint16",
            origin=file_dt,
        )
    ]
    interfaces = [
        PortInterface(
            path=f"{IF}/IfSpeed",
            name="IfSpeed",
            kind=InterfaceKind.SENDER_RECEIVER,
            elements=(DataElement(name="Speed", type_path=f"{DT}/uint16"),),
            origin=file_if,
        ),
        PortInterface(
            path=f"{IF}/IfTorque",
            name="IfTorque",
            kind=InterfaceKind.SENDER_RECEIVER,
            elements=(DataElement(name="Torque", type_path=f"{DT}/uint16"),),
            origin=file_if,
        ),
        PortInterface(
            path=f"{IF}/IfDiag",
            name="IfDiag",
            kind=InterfaceKind.CLIENT_SERVER,
            operations=(
                Operation(
                    name="ReadDtc",
                    arguments=(
                        Argument(
                            name="Dtc", direction=ArgumentDirection.OUT, type_path=f"{DT}/uint16"
                        ),
                    ),
                ),
            ),
            origin=file_if,
        ),
    ]
    components = [
        Component(
            path=f"{CT}/SwcA",
            name="SwcA",
            kind=ComponentKind.APPLICATION,
            team="Alpha",
            behavior_path=f"{CT}/SwcA/SwcA_InternalBehavior",
            origin=file_a,
        ),
        Component(
            path=f"{CT}/SwcB",
            name="SwcB",
            kind=ComponentKind.APPLICATION,
            team="Beta",
            behavior_path=f"{CT}/SwcB/SwcB_InternalBehavior",
            origin=file_b,
        ),
        Component(
            path=f"{CT}/SwcM",
            name="SwcM",
            kind=ComponentKind.APPLICATION,
            owner=ArxmlOwner.MODEL,
            team="Gamma",
            behavior_path=f"{CT}/SwcM/SwcM_InternalBehavior",
            origin=file_m,
        ),
        Component(
            path=COMPOSITION, name="EcuComposition", kind=ComponentKind.COMPOSITION, origin=file_c
        ),
    ]
    ports = [
        Port(
            path=f"{CT}/SwcA/PpSpeed",
            name="PpSpeed",
            swc_path=f"{CT}/SwcA",
            direction=PortDirection.P,
            interface_path=f"{IF}/IfSpeed",
            com_specs=(ComSpec(target="Speed", init_value="0"),),
            origin=file_a,
        ),
        Port(
            path=f"{CT}/SwcA/RpTorque",
            name="RpTorque",
            swc_path=f"{CT}/SwcA",
            direction=PortDirection.R,
            interface_path=f"{IF}/IfTorque",
            com_specs=(ComSpec(target="Torque", init_value="0"),),
            origin=file_a,
        ),
        Port(
            path=f"{CT}/SwcA/RpDiag",
            name="RpDiag",
            swc_path=f"{CT}/SwcA",
            direction=PortDirection.R,
            interface_path=f"{IF}/IfDiag",
            origin=file_a,
        ),
        Port(
            path=f"{CT}/SwcB/RpSpeed",
            name="RpSpeed",
            swc_path=f"{CT}/SwcB",
            direction=PortDirection.R,
            interface_path=f"{IF}/IfSpeed",
            com_specs=(ComSpec(target="Speed", init_value="0"),),
            origin=file_b,
        ),
        Port(
            path=f"{CT}/SwcB/PpDiag",
            name="PpDiag",
            swc_path=f"{CT}/SwcB",
            direction=PortDirection.P,
            interface_path=f"{IF}/IfDiag",
            origin=file_b,
        ),
        Port(
            path=f"{CT}/SwcM/PpTorque",
            name="PpTorque",
            swc_path=f"{CT}/SwcM",
            direction=PortDirection.P,
            interface_path=f"{IF}/IfTorque",
            com_specs=(ComSpec(target="Torque", init_value="0"),),
            origin=file_m,
        ),
    ]
    beh_a = f"{CT}/SwcA/SwcA_InternalBehavior"
    beh_b = f"{CT}/SwcB/SwcB_InternalBehavior"
    runnables = [
        Runnable(
            path=f"{beh_a}/RunA_10ms",
            name="RunA_10ms",
            swc_path=f"{CT}/SwcA",
            symbol="RunA_10ms",
            origin=file_a,
        ),
        Runnable(
            path=f"{beh_b}/RunB_OnSpeed",
            name="RunB_OnSpeed",
            swc_path=f"{CT}/SwcB",
            symbol="RunB_OnSpeed",
            origin=file_b,
        ),
        Runnable(
            path=f"{beh_b}/PpDiag_ReadDtc",
            name="PpDiag_ReadDtc",
            swc_path=f"{CT}/SwcB",
            symbol="PpDiag_ReadDtc",
            origin=file_b,
        ),
    ]
    access_points = [
        AccessPoint(
            path=f"{beh_a}/RunA_10ms/DWA_PpSpeed_Speed",
            swc_path=f"{CT}/SwcA",
            runnable_path=f"{beh_a}/RunA_10ms",
            port_path=f"{CT}/SwcA/PpSpeed",
            target="Speed",
            kind=AccessKind.IMPLICIT_WRITE,
            origin=file_a,
        ),
        AccessPoint(
            path=f"{beh_a}/RunA_10ms/SSCP_RpDiag_ReadDtc",
            swc_path=f"{CT}/SwcA",
            runnable_path=f"{beh_a}/RunA_10ms",
            port_path=f"{CT}/SwcA/RpDiag",
            target="ReadDtc",
            kind=AccessKind.SYNC_SERVER_CALL,
            origin=file_a,
        ),
        AccessPoint(
            path=f"{beh_b}/RunB_OnSpeed/DRPA_RpSpeed_Speed",
            swc_path=f"{CT}/SwcB",
            runnable_path=f"{beh_b}/RunB_OnSpeed",
            port_path=f"{CT}/SwcB/RpSpeed",
            target="Speed",
            kind=AccessKind.EXPLICIT_READ,
            origin=file_b,
        ),
    ]
    triggers = [
        Trigger(
            path=f"{beh_a}/TMT_RunA_10ms",
            name="TMT_RunA_10ms",
            swc_path=f"{CT}/SwcA",
            runnable_path=f"{beh_a}/RunA_10ms",
            kind=TriggerKind.PERIODIC,
            period_s=0.01,
            origin=file_a,
        ),
        Trigger(
            path=f"{beh_b}/DRT_RunB_OnSpeed",
            name="DRT_RunB_OnSpeed",
            swc_path=f"{CT}/SwcB",
            runnable_path=f"{beh_b}/RunB_OnSpeed",
            kind=TriggerKind.DATA_RECEIVED,
            port_path=f"{CT}/SwcB/RpSpeed",
            target="Speed",
            origin=file_b,
        ),
        Trigger(
            path=f"{beh_b}/OIT_PpDiag_ReadDtc",
            name="OIT_PpDiag_ReadDtc",
            swc_path=f"{CT}/SwcB",
            runnable_path=f"{beh_b}/PpDiag_ReadDtc",
            kind=TriggerKind.OPERATION_INVOKED,
            port_path=f"{CT}/SwcB/PpDiag",
            target="ReadDtc",
            origin=file_b,
        ),
    ]
    task_mappings = [
        TaskMapping(event_path=f"{beh_a}/TMT_RunA_10ms", task="Task_10ms", position=1),
        TaskMapping(event_path=f"{beh_b}/DRT_RunB_OnSpeed", task="Task_Event", position=1),
    ]
    prototypes = [
        ComponentPrototype(
            path=f"{COMPOSITION}/SwcA",
            name="SwcA",
            composition_path=COMPOSITION,
            type_path=f"{CT}/SwcA",
            origin=file_c,
        ),
        ComponentPrototype(
            path=f"{COMPOSITION}/SwcB",
            name="SwcB",
            composition_path=COMPOSITION,
            type_path=f"{CT}/SwcB",
            origin=file_c,
        ),
        ComponentPrototype(
            path=f"{COMPOSITION}/SwcM",
            name="SwcM",
            composition_path=COMPOSITION,
            type_path=f"{CT}/SwcM",
            origin=file_c,
        ),
    ]
    connectors = [
        Connector(
            path=f"{COMPOSITION}/SwcA_PpSpeed_SwcB_RpSpeed",
            name="SwcA_PpSpeed_SwcB_RpSpeed",
            composition_path=COMPOSITION,
            kind=ConnectorKind.ASSEMBLY,
            provider=ConnectorEnd(
                prototype_path=f"{COMPOSITION}/SwcA", port_path=f"{CT}/SwcA/PpSpeed"
            ),
            requester=ConnectorEnd(
                prototype_path=f"{COMPOSITION}/SwcB", port_path=f"{CT}/SwcB/RpSpeed"
            ),
            origin=file_c,
        ),
        Connector(
            path=f"{COMPOSITION}/SwcB_PpDiag_SwcA_RpDiag",
            name="SwcB_PpDiag_SwcA_RpDiag",
            composition_path=COMPOSITION,
            kind=ConnectorKind.ASSEMBLY,
            provider=ConnectorEnd(
                prototype_path=f"{COMPOSITION}/SwcB", port_path=f"{CT}/SwcB/PpDiag"
            ),
            requester=ConnectorEnd(
                prototype_path=f"{COMPOSITION}/SwcA", port_path=f"{CT}/SwcA/RpDiag"
            ),
            origin=file_c,
        ),
    ]
    files = [
        SourceFile(rel_path=o.file, size=1000 + i, mtime_ns=1_700_000_000_000_000_000 + i)
        for i, o in enumerate((file_a, file_b, file_m, file_c, file_if, file_dt))
    ]
    return Model(
        components=components,
        ports=ports,
        interfaces=interfaces,
        runnables=runnables,
        access_points=access_points,
        triggers=triggers,
        task_mappings=task_mappings,
        tasks=("Task_10ms", "Task_Event", "Task_Init"),
        compositions=[Composition(path=COMPOSITION, name="EcuComposition", origin=file_c)],
        prototypes=prototypes,
        connectors=connectors,
        data_types=data_types,
        base_types=base_types,
        files=files,
        baseline_commit="0123456789abcdef0123456789abcdef01234567",
    )


def build_sample_config(repo_root: Path) -> ProjectConfig:
    """A ProjectConfig that matches ``build_sample_model`` (no files are created)."""
    owners = {
        "SwcA": OwnerEntry(swc="SwcA", team="Alpha"),
        "SwcB": OwnerEntry(swc="SwcB", team="Beta"),
        "SwcM": OwnerEntry(
            swc="SwcM",
            team="Gamma",
            arxml_owner=ArxmlOwner.MODEL,
            authoring_tool="Simulink",
            export_folder="Models/SwcM/arxml",
        ),
    }
    return ProjectConfig(
        repo_root=repo_root,
        arxml_roots=(repo_root / "Config" / "Developer",),
        owners=MappingProxyType(owners),
    )
