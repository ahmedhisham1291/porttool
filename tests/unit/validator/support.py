"""Test support for the validator: a richer model, row builders and outcome checks.

The model extends ``tests.helpers.sample_model`` (SwcA, SwcB, SwcM, EcuComposition):

    SwcC, SwcD   application SWCs (DaVinci-owned) with ports of every interface kind
    SwcE         application SWC that lives only in SubComposition
    SwcL         locked by the model (owner LOCKED); SwcLockedCfg locked by locked.txt
    SwcSrv       service SWC
    /Pkg1/SwcDup, /Pkg2/SwcDup   two SWCs with the same short name
    SubComposition  (SwcE, ProtoX, ProtoY)   SubComposition2 (ProtoX, ProtoY)
    LockedComposition (LA, LB), locked by locked.txt
    /X/DupComp, /Y/DupComp       two compositions with the same short name
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

from porttool.core.config import NamingRules, OwnerEntry, ProjectConfig
from porttool.core.interfaces import ValidationOutcome
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
    CompuMethod,
    ComSpec,
    Connector,
    ConnectorEnd,
    ConnectorKind,
    DataConstraint,
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
    TaskMapping,
    Trigger,
    TriggerKind,
    Unit,
)
from porttool.core.operations import APPLY_ORDER, AnyOperation, ChangeSet, Route
from porttool.core.pending import PendingEntry, PendingQueue, PendingState
from porttool.core.results import (
    Finding,
    RawRequestRow,
    RequestStatus,
    RowRef,
    Severity,
)
from porttool.core.rowid import row_id
from porttool.core.sheets import Sheet
from porttool.validator import ALL_CODES, validate
from tests.helpers.sample_model import (
    COMPOSITION,
    CT,
    DT,
    IF,
    build_sample_config,
    build_sample_model,
)

BT = f"{DT}/BaseTypes"
SUB = f"{CT}/SubComposition"
SUB2 = f"{CT}/SubComposition2"
LOCKED_COMPOSITION = f"{CT}/LockedComposition"
MODES = "/Modes/EcuModes"
OTHER_MODES = "/Modes/OtherModes"
NOW = "2026-11-11T08:00:00Z"
ALPHA = "C:\\requests\\team_alpha.xlsx"
BETA = "C:\\requests\\team_beta.xlsx"
_ORIGIN = Origin(file="Config/Developer/Extra.arxml")

A = RequestStatus.ACCEPTED
R = RequestStatus.REJECTED
D = RequestStatus.ALREADY_APPLIED
W = RequestStatus.WAITING_FOR_MODEL_OWNER
H = RequestStatus.ON_HOLD


def behavior(swc: str) -> str:
    return f"{CT}/{swc}/{swc}_InternalBehavior"


def _value_type(name: str, base: str | None = None, **more: object) -> DataType:
    return DataType(
        path=f"{DT}/{name}",
        name=name,
        level=DataTypeLevel.IMPLEMENTATION,
        category=DataTypeCategory.VALUE,
        base_type_path=f"{BT}/{base}" if base else None,
        origin=_ORIGIN,
        **more,  # type: ignore[arg-type]
    )


def _sr(
    name: str, *elements: tuple[str, str, bool], kind: InterfaceKind | None = None
) -> PortInterface:
    return PortInterface(
        path=f"{IF}/{name}",
        name=name,
        kind=kind or InterfaceKind.SENDER_RECEIVER,
        elements=tuple(
            DataElement(name=element, type_path=type_path, is_queued=queued)
            for element, type_path, queued in elements
        ),
        origin=_ORIGIN,
    )


def _port(swc: str, name: str, direction: str, interface: str, init: str | None = None) -> Port:
    interface_path = interface if interface.startswith("/") else f"{IF}/{interface}"
    return Port(
        path=f"{CT}/{swc}/{name}",
        name=name,
        swc_path=f"{CT}/{swc}",
        direction=PortDirection(direction),
        interface_path=interface_path,
        com_specs=(ComSpec(target=init.split("=")[0], init_value=init.split("=")[1]),)
        if init
        else (),
        origin=_ORIGIN,
    )


def _swc(name: str, **more: object) -> Component:
    values: dict[str, object] = {
        "path": f"{CT}/{name}",
        "name": name,
        "kind": ComponentKind.APPLICATION,
        "behavior_path": behavior(name),
        "origin": _ORIGIN,
    }
    values.update(more)
    return Component(**values)  # type: ignore[arg-type]


def _runnable(swc: str, name: str) -> Runnable:
    return Runnable(
        path=f"{behavior(swc)}/{name}",
        name=name,
        swc_path=f"{CT}/{swc}",
        symbol=name,
        origin=_ORIGIN,
    )


def _trigger(
    swc: str,
    name: str,
    runnable: str,
    kind: TriggerKind,
    port: str | None = None,
    target: str | None = None,
    period_s: float | None = None,
) -> Trigger:
    return Trigger(
        path=f"{behavior(swc)}/{name}",
        name=name,
        swc_path=f"{CT}/{swc}",
        runnable_path=f"{behavior(swc)}/{runnable}",
        kind=kind,
        port_path=f"{CT}/{swc}/{port}" if port else None,
        target=target,
        period_s=period_s,
        origin=_ORIGIN,
    )


def _access(
    swc: str, runnable: str, name: str, port: str, target: str, kind: AccessKind
) -> AccessPoint:
    return AccessPoint(
        path=f"{behavior(swc)}/{runnable}/{name}",
        swc_path=f"{CT}/{swc}",
        runnable_path=f"{behavior(swc)}/{runnable}",
        port_path=f"{CT}/{swc}/{port}",
        target=target,
        kind=kind,
        origin=_ORIGIN,
    )


def _prototype(composition: str, name: str, swc_type: str) -> ComponentPrototype:
    return ComponentPrototype(
        path=f"{composition}/{name}",
        name=name,
        composition_path=composition,
        type_path=swc_type if swc_type.startswith("/") else f"{CT}/{swc_type}",
        origin=_ORIGIN,
    )


def _connector(
    composition: str, provider: str, provider_port: str, requester: str, requester_port: str
) -> Connector:
    name = f"{provider}_{provider_port}_{requester}_{requester_port}"
    prototypes = {
        "LA": "SwcC",
        "LB": "SwcD",
    }
    return Connector(
        path=f"{composition}/{name}",
        name=name,
        composition_path=composition,
        kind=ConnectorKind.ASSEMBLY,
        provider=ConnectorEnd(
            prototype_path=f"{composition}/{provider}",
            port_path=f"{CT}/{prototypes.get(provider, provider)}/{provider_port}",
        ),
        requester=ConnectorEnd(
            prototype_path=f"{composition}/{requester}",
            port_path=f"{CT}/{prototypes.get(requester, requester)}/{requester_port}",
        ),
        origin=_ORIGIN,
    )


def build_rich_model() -> Model:
    """The sample model plus everything the validator rules need to be exercised."""
    base = build_sample_model()
    u16, u8 = f"{DT}/uint16", f"{DT}/uint8"

    base_types = [
        BaseType(f"{BT}/uint8", "uint8", 8, "NONE", _ORIGIN),
        BaseType(f"{BT}/sint8", "sint8", 8, "2C", _ORIGIN),
        BaseType(f"{BT}/uint32", "uint32", 32, "NONE", _ORIGIN),
        BaseType(f"{BT}/float32", "float32", 32, "IEEE754", _ORIGIN),
        BaseType(f"{BT}/boolean", "boolean", 8, "BOOLEAN", _ORIGIN),
        BaseType(f"{BT}/bcd8", "bcd8", 8, "BCD-P", _ORIGIN),
        BaseType("/Other/BaseTypes/dupbase", "dupbase", 8, "NONE", _ORIGIN),
        BaseType("/Third/BaseTypes/dupbase", "dupbase", 8, "NONE", _ORIGIN),
    ]
    units = [
        Unit(f"{DT}/Units/Pct", "Pct", "%", _ORIGIN),
        Unit(f"{DT}/Units/Kmh", "Kmh", "km/h", _ORIGIN),
        Unit("/Other/Units/Dupunit", "Dupunit", None, _ORIGIN),
        Unit("/Third/Units/Dupunit", "Dupunit", None, _ORIGIN),
    ]
    compu_methods = [
        CompuMethod(
            path=f"{DT}/CompuMethods/EngineState_Cm",
            name="EngineState_Cm",
            category="TEXTTABLE",
            enum_values=((0, "OFF"), (1, "RUN"), (2, "FAULT")),
            origin=_ORIGIN,
        ),
        CompuMethod(
            path=f"{DT}/CompuMethods/Percent_Cm",
            name="Percent_Cm",
            category="LINEAR",
            factor=0.5,
            offset=0.0,
            unit_path=f"{DT}/Units/Pct",
            origin=_ORIGIN,
        ),
    ]
    constraints = [
        DataConstraint(f"{DT}/DataConstrs/Percent_Dc", "Percent_Dc", 0.0, 100.0, _ORIGIN),
        DataConstraint(f"{DT}/DataConstrs/Limited_Dc", "Limited_Dc", 10.0, 20.0, _ORIGIN),
    ]
    data_types = [
        _value_type("uint8", "uint8"),
        _value_type("sint8", "sint8"),
        _value_type("uint32", "uint32"),
        _value_type("float32", "float32"),
        replace(_value_type("boolean", "boolean"), category=DataTypeCategory.BOOLEAN),
        replace(
            _value_type("EngineState", "uint8"),
            category=DataTypeCategory.ENUMERATION,
            compu_method_path=f"{DT}/CompuMethods/EngineState_Cm",
        ),
        replace(
            _value_type("Percent"),
            level=DataTypeLevel.APPLICATION,
            compu_method_path=f"{DT}/CompuMethods/Percent_Cm",
            constraint_path=f"{DT}/DataConstrs/Percent_Dc",
            unit_path=f"{DT}/Units/Pct",
        ),
        replace(_value_type("Limited", "uint8"), constraint_path=f"{DT}/DataConstrs/Limited_Dc"),
        replace(
            _value_type("SpeedArray"),
            category=DataTypeCategory.ARRAY,
            element_type_paths=(u16,),
            array_size=4,
        ),
        replace(
            _value_type("SpeedRec"),
            category=DataTypeCategory.RECORD,
            element_type_paths=(u16, u8),
            element_names=("a", "b"),
        ),
        replace(_value_type("AppSpeed"), level=DataTypeLevel.APPLICATION),
        replace(
            _value_type("TypeRef"),
            category=DataTypeCategory.TYPE_REFERENCE,
            element_type_paths=(u8,),
        ),
        replace(_value_type("Dup", "uint8"), path="/Other/Dup"),
        replace(_value_type("Dup", "uint8"), path="/Third/Dup"),
    ]
    read_dtc = Operation(name="ReadDtc", arguments=(Argument("Dtc", ArgumentDirection.OUT, u16),))
    interfaces = [
        _sr("IfQueued", ("Event", u8, True)),
        _sr("IfMulti", ("A", u16, False), ("B", u8, False)),
        _sr("IfMixed", ("Value", u16, False), ("Evt", u8, True)),
        _sr("IfState", ("State", f"{DT}/EngineState", False)),
        _sr("IfPercent", ("Pct", f"{DT}/Percent", False)),
        _sr("IfArr", ("Arr", f"{DT}/SpeedArray", False)),
        _sr("IfLimited", ("Lim", f"{DT}/Limited", False)),
        _sr("IfBool", ("Flag", f"{DT}/boolean", False)),
        _sr("IfFloat", ("F", f"{DT}/float32", False)),
        _sr("IfSigned", ("S", f"{DT}/sint8", False)),
        _sr("IfRef", ("Ref", f"{DT}/TypeRef", False)),
        _sr("IfSpeedCopy", ("Speed", u16, False)),
        _sr("IfSpeedWide", ("Speed", u16, False), ("Extra", u8, False)),
        _sr("IfSpeed8", ("Speed", u8, False)),
        _sr("IfSpeedQueued", ("Speed", u16, True)),
        _sr("IfUnknownType", ("X", "/Missing/Type", False)),
        _sr("IfParam", ("Gain", u16, False), kind=InterfaceKind.PARAMETER),
        _sr("IfNv", ("Stored", u16, False), kind=InterfaceKind.NV_DATA),
        replace(_sr("IfDup", ("V", u8, False)), path="/Other/IfDup"),
        replace(_sr("IfDup", ("V", u8, False)), path="/Third/IfDup"),
        PortInterface(
            path=f"{IF}/IfMode",
            name="IfMode",
            kind=InterfaceKind.MODE_SWITCH,
            elements=(DataElement(name="currentMode", type_path=MODES),),
            mode_group_path=MODES,
            origin=_ORIGIN,
        ),
        PortInterface(
            path=f"{IF}/IfMode2",
            name="IfMode2",
            kind=InterfaceKind.MODE_SWITCH,
            mode_group_path=OTHER_MODES,
            origin=_ORIGIN,
        ),
        PortInterface(
            path=f"{IF}/IfDiag2",
            name="IfDiag2",
            kind=InterfaceKind.CLIENT_SERVER,
            operations=(read_dtc, Operation(name="Clear")),
            origin=_ORIGIN,
        ),
        PortInterface(
            path=f"{IF}/IfDiagAlt",
            name="IfDiagAlt",
            kind=InterfaceKind.CLIENT_SERVER,
            operations=(
                Operation(name="ReadDtc", arguments=(Argument("Dtc", ArgumentDirection.IN, u16),)),
            ),
            origin=_ORIGIN,
        ),
    ]
    components = [
        _swc("SwcC"),
        _swc("SwcD"),
        _swc("SwcE"),
        _swc("SwcL", owner=ArxmlOwner.LOCKED),
        _swc("SwcLockedCfg"),
        _swc("SwcSrv", kind=ComponentKind.SERVICE),
        _swc("SwcDup", path="/Pkg1/SwcDup", behavior_path=None),
        _swc("SwcDup", path="/Pkg2/SwcDup", behavior_path=None),
        _swc("SubComposition", kind=ComponentKind.COMPOSITION, behavior_path=None),
        _swc("SubComposition2", kind=ComponentKind.COMPOSITION, behavior_path=None),
        _swc("LockedComposition", kind=ComponentKind.COMPOSITION, behavior_path=None),
    ]
    ports = [
        _port("SwcC", "PpMode", "P", "IfMode"),
        _port("SwcC", "PrSpeed", "PR", "IfSpeed", "Speed=0"),
        _port("SwcC", "RpParam", "R", "IfParam"),
        _port("SwcC", "RpMulti", "R", "IfMulti"),
        _port("SwcC", "PpSpeedC", "P", "IfSpeed", "Speed=0"),
        _port("SwcC", "PpDiagC", "P", "IfDiag"),
        _port("SwcC", "PpDiagAlt", "P", "IfDiagAlt"),
        _port("SwcC", "RpGhost", "R", "/Missing/IfGhost"),
        _port("SwcC", "RpQueuedC", "R", "IfQueued"),
        _port("SwcC", "RpMixed", "R", "IfMixed", "Value=0"),
        _port("SwcC", "PpWide", "P", "IfSpeedWide"),
        _port("SwcC", "PpSpeed8", "P", "IfSpeed8", "Speed=0"),
        _port("SwcC", "RpNv", "R", "IfNv"),
        _port("SwcC", "RpFree", "R", "IfSpeed", "Speed=7"),
        _port("SwcC", "RpDiagC", "R", "IfDiag"),
        _port("SwcC", "RpModeC", "R", "IfMode"),
        _port("SwcD", "RpMode", "R", "IfMode"),
        _port("SwcD", "PpMode2", "P", "IfMode"),
        _port("SwcD", "PpQueued", "P", "IfQueued"),
        _port("SwcD", "PpMulti", "P", "IfMulti"),
        _port("SwcD", "RpSpeedD", "R", "IfSpeed", "Speed=0"),
        _port("SwcD", "PpSpeedD", "P", "IfSpeed", "Speed=0"),
        _port("SwcD", "RpModeOther", "R", "IfMode2"),
        _port("SwcD", "RpSpeedCopy", "R", "IfSpeedCopy", "Speed=0"),
        _port("SwcD", "RpDiagD", "R", "IfDiag"),
        _port("SwcD", "RpDiag2D", "R", "IfDiag2"),
        _port("SwcD", "RpSpeedQ", "R", "IfSpeedQueued"),
        _port("SwcD", "PpUnused", "P", "IfSpeed", "Speed=0"),
        _port("SwcE", "PpSpeedE", "P", "IfSpeed", "Speed=0"),
        _port("SwcL", "PpLocked", "P", "IfSpeed", "Speed=0"),
        _port("SwcLockedCfg", "PpLockedCfg", "P", "IfSpeed", "Speed=0"),
        _port("SwcSrv", "PpService", "P", "IfSpeed", "Speed=0"),
        _port("SwcM", "RpSpeedM", "R", "IfSpeed", "Speed=0"),
    ]
    runnables = [
        _runnable("SwcC", "RunC"),
        _runnable("SwcC", "RunC_Unmapped"),
        _runnable("SwcC", "RunC_Plain"),
        _runnable("SwcC", "PpDiagC_ReadDtc"),
        _runnable("SwcD", "RunD"),
        _runnable("SwcM", "RunM"),
        _runnable("SwcL", "RunL"),
    ]
    triggers = [
        _trigger("SwcC", "TMT_RunC_10ms", "RunC", TriggerKind.PERIODIC, period_s=0.01),
        _trigger("SwcC", "DRT_RunC_Mixed", "RunC", TriggerKind.DATA_RECEIVED, "RpMixed", "Value"),
        _trigger("SwcC", "TMT_RunC_Unmapped", "RunC_Unmapped", TriggerKind.PERIODIC, period_s=0.1),
        _trigger(
            "SwcC",
            "OIT_PpDiagC_ReadDtc",
            "PpDiagC_ReadDtc",
            TriggerKind.OPERATION_INVOKED,
            "PpDiagC",
            "ReadDtc",
        ),
        _trigger("SwcD", "TMT_RunD_20ms", "RunD", TriggerKind.PERIODIC, period_s=0.02),
        _trigger("SwcD", "DRT_RunD_Speed", "RunD", TriggerKind.DATA_RECEIVED, "RpSpeedD", "Speed"),
        _trigger("SwcM", "TMT_RunM_10ms", "RunM", TriggerKind.PERIODIC, period_s=0.01),
        _trigger("SwcL", "TMT_RunL_10ms", "RunL", TriggerKind.PERIODIC, period_s=0.01),
    ]
    task_mappings = [
        TaskMapping(f"{behavior('SwcC')}/TMT_RunC_10ms", "Task_10ms", 3),
        TaskMapping(f"{behavior('SwcC')}/DRT_RunC_Mixed", "Task_Event", 2),
        TaskMapping(f"{behavior('SwcD')}/TMT_RunD_20ms", "Task_10ms", 2),
        TaskMapping(f"{behavior('SwcD')}/DRT_RunD_Speed", "Task_10ms", 5),
        TaskMapping(f"{behavior('SwcM')}/TMT_RunM_10ms", "Task_10ms", 4),
    ]
    access_points = [
        _access("SwcC", "RunC", "DRA_RpMulti_A", "RpMulti", "A", AccessKind.IMPLICIT_READ),
        _access("SwcC", "RunC", "DSP_PrSpeed_Speed", "PrSpeed", "Speed", AccessKind.EXPLICIT_SEND),
        _access(
            "SwcD", "RunD", "DRPA_RpSpeedD_Speed", "RpSpeedD", "Speed", AccessKind.EXPLICIT_READ
        ),
    ]
    compositions = [
        Composition(SUB, "SubComposition", _ORIGIN),
        Composition(SUB2, "SubComposition2", _ORIGIN),
        Composition(LOCKED_COMPOSITION, "LockedComposition", _ORIGIN),
        Composition("/X/DupComp", "DupComp", _ORIGIN),
        Composition("/Y/DupComp", "DupComp", _ORIGIN),
    ]
    prototypes = [
        _prototype(COMPOSITION, "SwcC", "SwcC"),
        _prototype(COMPOSITION, "SwcD", "SwcD"),
        _prototype(COMPOSITION, "SwcL", "SwcL"),
        _prototype(COMPOSITION, "SwcSrv", "SwcSrv"),
        _prototype(COMPOSITION, "SwcLockedCfg", "SwcLockedCfg"),
        _prototype(COMPOSITION, "Ghost", "/Missing/SwcGhost"),
        _prototype(SUB, "SwcE", "SwcE"),
        _prototype(SUB, "ProtoX", "SwcC"),
        _prototype(SUB, "ProtoY", "SwcD"),
        _prototype(SUB2, "ProtoX", "SwcC"),
        _prototype(SUB2, "ProtoY", "SwcD"),
        _prototype(LOCKED_COMPOSITION, "LA", "SwcC"),
        _prototype(LOCKED_COMPOSITION, "LB", "SwcD"),
        _prototype("/X/DupComp", "DA", "SwcC"),
        _prototype("/X/DupComp", "DB", "SwcD"),
    ]
    connectors = [
        _connector(COMPOSITION, "SwcC", "PpSpeedC", "SwcD", "RpSpeedD"),
        _connector(COMPOSITION, "SwcC", "PpMode", "SwcD", "RpMode"),
        _connector(LOCKED_COMPOSITION, "LA", "PpSpeedC", "LB", "RpSpeedD"),
    ]
    return Model(
        components=[*base.components.values(), *components],
        ports=[*base.ports.values(), *ports],
        interfaces=[*base.interfaces.values(), *interfaces],
        runnables=[*base.runnables.values(), *runnables],
        access_points=[*base.access_points.values(), *access_points],
        triggers=[*base.triggers.values(), *triggers],
        task_mappings=[*base.task_mappings.values(), *task_mappings],
        tasks=base.tasks,
        compositions=[*base.compositions.values(), *compositions],
        prototypes=[*base.prototypes.values(), *prototypes],
        connectors=[*base.connectors.values(), *connectors],
        data_types=[*base.data_types.values(), *data_types],
        base_types=[*base.base_types.values(), *base_types],
        compu_methods=compu_methods,
        constraints=constraints,
        units=units,
        files=base.files.values(),
        baseline_commit=base.baseline_commit,
    )


def build_rich_config(**changes: object) -> ProjectConfig:
    """Configuration for ``build_rich_model``. ``changes`` replace fields of ProjectConfig."""
    sample = build_sample_config(Path("C:/repo"))
    owners = dict(sample.owners)
    owners["SwcC"] = OwnerEntry(swc="SwcC", team="Gamma")
    config = replace(
        sample,
        owners=MappingProxyType(owners),
        locked_swcs=frozenset({"SwcLockedCfg", "LockedComposition"}),
    )
    return replace(config, **changes) if changes else config  # type: ignore[arg-type]


def naming(**rules: object) -> NamingRules:
    return replace(NamingRules(), **rules)  # type: ignore[arg-type]


MODEL = build_rich_model()
CONFIG = build_rich_config()


# ---------------------------------------------------------------------- rows


def _row(
    sheet: Sheet,
    values: dict[str, str],
    action: str,
    rid: str | None,
    file: str,
    requester: str,
) -> RawRequestRow:
    return RawRequestRow(
        sheet=sheet.value,
        row=RowRef(file=file, sheet=sheet.value, row=0),
        row_id=rid,
        action=action,
        requester=requester,
        comment="",
        values=MappingProxyType({key: value for key, value in values.items() if value != ""}),
    )


def port_row(
    swc: str,
    port: str,
    direction: str = "R",
    interface: str = "IfSpeed",
    init: str = "0",
    *,
    action: str = "ADD",
    rid: str | None = None,
    file: str = ALPHA,
    requester: str = "alice",
) -> RawRequestRow:
    values = {
        "swc": swc,
        "port": port,
        "direction": direction,
        "interface": interface,
        "init_value": init,
    }
    return _row(Sheet.PORTS, values, action, rid, file, requester)


def type_row(
    name: str,
    category: str = "value",
    level: str = "implementation",
    *,
    action: str = "ADD",
    rid: str | None = None,
    file: str = ALPHA,
    **cells: str,
) -> RawRequestRow:
    values = {"name": name, "level": level, "category": category, **cells}
    return _row(Sheet.DATA_TYPES, values, action, rid, file, "alice")


def interface_row(
    name: str,
    kind: str = "sender-receiver",
    *,
    action: str = "ADD",
    rid: str | None = None,
    file: str = ALPHA,
) -> RawRequestRow:
    return _row(Sheet.INTERFACES, {"name": name, "kind": kind}, action, rid, file, "alice")


def element_row(
    interface: str,
    element: str,
    data_type: str = "uint16",
    *,
    queued: str = "",
    argument: str = "",
    direction: str = "",
    action: str = "ADD",
    rid: str | None = None,
    file: str = ALPHA,
) -> RawRequestRow:
    values = {
        "interface": interface,
        "element": element,
        "argument": argument,
        "direction": direction,
        "data_type": data_type,
        "queued": queued,
    }
    return _row(Sheet.INTERFACE_ELEMENTS, values, action, rid, file, "alice")


def access_row(
    swc: str,
    runnable: str,
    port: str,
    target: str,
    kind: str,
    *,
    action: str = "ADD",
    rid: str | None = None,
    file: str = ALPHA,
) -> RawRequestRow:
    values = {
        "swc": swc,
        "runnable": runnable,
        "port": port,
        "target": target,
        "access_kind": kind,
    }
    return _row(Sheet.RUNNABLE_ACCESS, values, action, rid, file, "alice")


def trigger_row(
    swc: str,
    runnable: str,
    kind: str,
    port: str = "",
    target: str = "",
    *,
    period_ms: str = "",
    task: str = "",
    action: str = "ADD",
    rid: str | None = None,
    file: str = ALPHA,
) -> RawRequestRow:
    values = {
        "swc": swc,
        "runnable": runnable,
        "trigger_kind": kind,
        "port": port,
        "target": target,
        "period_ms": period_ms,
        "task": task,
    }
    return _row(Sheet.RUNNABLE_TRIGGERS, values, action, rid, file, "alice")


def connection_row(
    provider: str,
    provider_port: str,
    requester: str,
    requester_port: str,
    *,
    composition: str = "",
    name: str = "",
    action: str = "ADD",
    rid: str | None = None,
    file: str = ALPHA,
) -> RawRequestRow:
    values = {
        "provider_swc": provider,
        "provider_port": provider_port,
        "requester_swc": requester,
        "requester_port": requester_port,
        "composition": composition,
        "connector_name": name,
    }
    return _row(Sheet.CONNECTIONS, values, action, rid, file, "alice")


def raw_row(
    sheet: str, action: str = "ADD", rid: str | None = None, **values: str
) -> RawRequestRow:
    """A row on any sheet name, for the row-level rules."""
    return RawRequestRow(
        sheet=sheet,
        row=RowRef(file=ALPHA, sheet=sheet, row=0),
        row_id=rid,
        action=action,
        requester="alice",
        comment="",
        values=MappingProxyType(dict(values)),
    )


def port_rid(swc: str, port: str) -> str:
    return row_id(Sheet.PORTS, f"{CT}/{swc}/{port}")


def connector_rid(name: str, composition: str = COMPOSITION) -> str:
    return row_id(Sheet.CONNECTIONS, f"{composition}/{name}")


def access_rid(swc: str, runnable: str, name: str) -> str:
    return row_id(Sheet.RUNNABLE_ACCESS, f"{behavior(swc)}/{runnable}/{name}")


def trigger_rid(swc: str, name: str) -> str:
    return row_id(Sheet.RUNNABLE_TRIGGERS, f"{behavior(swc)}/{name}")


def number(rows: Iterable[RawRequestRow]) -> tuple[RawRequestRow, ...]:
    """Give rows without a row number the next free number of their workbook sheet."""
    counters: dict[tuple[str, str], int] = {}
    numbered: list[RawRequestRow] = []
    for row in rows:
        if row.row.row:
            numbered.append(row)
            continue
        key = (row.row.file, row.row.sheet)
        counters[key] = counters.get(key, 1) + 1
        numbered.append(replace(row, row=replace(row.row, row=counters[key])))
    return tuple(numbered)


# ---------------------------------------------------------------------- running and checking


def run(
    *rows: RawRequestRow,
    model: Model = MODEL,
    config: ProjectConfig = CONFIG,
    pending: PendingQueue | None = None,
    now: str = NOW,
) -> ValidationOutcome:
    """Validate ``rows`` and check the invariants that hold for every outcome."""
    numbered = number(rows)
    queue = pending or PendingQueue()
    outcome = validate(model, config, numbered, queue, now)
    check_invariants(outcome, numbered, model, queue, now)
    return outcome


def all_findings(outcome: ValidationOutcome) -> list[Finding]:
    found = [finding for result in outcome.results for finding in result.findings]
    found.extend(outcome.findings)
    return found


def codes_of(outcome: ValidationOutcome) -> set[str]:
    return {finding.code for finding in all_findings(outcome)}


def statuses(outcome: ValidationOutcome) -> tuple[RequestStatus, ...]:
    return tuple(result.status for result in outcome.results)


def op_kinds(outcome: ValidationOutcome) -> list[str]:
    return [op.op for op in outcome.change_set.operations]


def messages(outcome: ValidationOutcome, code: str) -> list[str]:
    return [finding.message for finding in all_findings(outcome) if finding.code == code]


def operation(outcome: ValidationOutcome, kind: str, index: int = 0) -> AnyOperation:
    found = [op for op in outcome.change_set.operations if op.op == kind]
    assert len(found) > index, f"no operation {kind}[{index}] in {op_kinds(outcome)}"
    return found[index]


def waiting_entry(
    op: AnyOperation, created: str = "2026-11-01T08:00:00Z", **more: object
) -> PendingEntry:
    state = PendingState.ON_HOLD if op.route is Route.HOLD else PendingState.WAITING_FOR_MODEL_OWNER
    return PendingEntry(operation=op, state=state, created_utc=created, **more)  # type: ignore[arg-type]


def queue_of(*entries: PendingEntry) -> PendingQueue:
    return PendingQueue(entries=tuple(entries))


def check_invariants(
    outcome: ValidationOutcome,
    rows: Sequence[RawRequestRow],
    model: Model,
    pending: PendingQueue,
    now: str,
) -> None:
    """Properties of every validation outcome, whatever the requests were."""
    change_set = outcome.change_set
    assert change_set.baseline_commit == model.baseline_commit
    assert change_set.model_hash == model.model_hash
    assert change_set.created_utc == now

    assert len(outcome.results) == len(rows), "one result per request row"
    request_ids = [result.request_id for result in outcome.results]
    assert len(set(request_ids)) == len(request_ids), "request ids are unique"
    for result, row in zip(outcome.results, rows, strict=True):
        assert result.row == row.row
        assert result.status is not RequestStatus.APPLIED
        errors = [f for f in result.findings if f.severity is Severity.ERROR]
        if result.status is RequestStatus.REJECTED:
            assert errors, f"rejected without an error finding: {row}"
            assert not result.operation_ids
        else:
            assert not errors, f"{result.status} with error findings: {errors}"
        for finding in result.findings:
            assert finding.row == row.row
            assert finding.code in ALL_CODES
            assert finding.message.strip()

    operations = change_set.operations
    ids = [op.id for op in operations]
    assert len(set(ids)) == len(ids), "operation ids are unique"
    positions = [APPLY_ORDER.index(op.op) for op in operations]
    assert positions == sorted(positions), "operations follow APPLY_ORDER"
    by_id = {op.id: op for op in operations}
    referenced: set[str] = set()
    for result in outcome.results:
        routes = set()
        for op_id in result.operation_ids:
            assert op_id in by_id, f"result refers to unknown operation {op_id}"
            routes.add(by_id[op_id].route)
            referenced.add(op_id)
        if result.status is RequestStatus.ACCEPTED:
            assert result.operation_ids and routes == {Route.APPLY}
        elif result.status is RequestStatus.WAITING_FOR_MODEL_OWNER:
            assert Route.MODEL in routes and Route.HOLD not in routes
        elif result.status is RequestStatus.ON_HOLD:
            assert Route.HOLD in routes
        else:
            assert not result.operation_ids
    old_ids = {entry.operation.id for entry in pending.entries}
    for op in operations:
        assert op.id in referenced or op.id in old_ids, f"operation {op.id} belongs to no request"

    queued = {entry.operation.id for entry in outcome.pending.entries}
    assert len(queued) == len(outcome.pending.entries), "no request is queued twice"
    for op in operations:
        if op.route is not Route.APPLY:
            assert op.id in queued, f"{op.route} operation {op.id} is not in the pending queue"
    for entry in outcome.pending.entries:
        assert entry.operation.route is not Route.APPLY
        assert entry.created_utc
    for finding in outcome.findings:
        assert finding.code in ALL_CODES
        assert finding.row is None

    assert ChangeSet.from_json(change_set.to_json()) == change_set
    assert PendingQueue.from_json(outcome.pending.to_json()) == outcome.pending


Tweak = Callable[[ProjectConfig], ProjectConfig]
