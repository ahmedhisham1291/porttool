"""The rule table: one small import per case, with the statuses and codes it must produce.

``fail`` lists codes the case must report; ``ok`` lists codes whose rule the case
exercises and that must NOT be reported. ``test_rule_table`` checks that every code of
``porttool.validator.codes`` appears in at least one ``fail`` and one ``ok``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from porttool.core.models import AccessKind, PortDirection, TriggerKind
from porttool.core.operations import (
    AddAccessPoint,
    AddConnector,
    AddPort,
    AddServerRunnable,
    AddTrigger,
    AnyOperation,
    DeletePort,
    InitValueSpec,
    Route,
)
from porttool.core.pending import PendingEntry
from porttool.core.results import RawRequestRow, RequestStatus
from porttool.validator import codes as c
from tests.helpers.sample_model import COMPOSITION, CT, IF
from tests.unit.validator.support import (
    A,
    BETA,
    D,
    H,
    LOCKED_COMPOSITION,
    R,
    W,
    access_rid,
    access_row,
    connection_row,
    connector_rid,
    element_row,
    interface_row,
    naming,
    port_rid,
    port_row,
    raw_row,
    trigger_rid,
    trigger_row,
    type_row,
    waiting_entry,
)


@dataclass(frozen=True)
class Case:
    name: str
    rows: tuple[RawRequestRow, ...]
    expect: tuple[RequestStatus, ...]
    fail: tuple[str, ...] = ()
    ok: tuple[str, ...] = ()
    pending: tuple[PendingEntry, ...] = ()
    config: dict[str, object] = field(default_factory=dict)
    now: str | None = None


def case(
    name: str,
    rows: list[RawRequestRow],
    expect: list[RequestStatus],
    fail: tuple[str, ...] = (),
    ok: tuple[str, ...] = (),
    pending: tuple[PendingEntry, ...] = (),
    now: str | None = None,
    **config: object,
) -> Case:
    return Case(name, tuple(rows), tuple(expect), fail, ok, pending, config, now)


def _pend_port(
    swc: str, name: str, direction: str = "P", interface: str = "IfTorque", **more: object
) -> PendingEntry:
    op = AddPort(
        id=f"Ports:add_port:{CT}/{swc}/{name}",
        route=Route.MODEL,
        requester="bob",
        swc_path=f"{CT}/{swc}",
        name=name,
        direction=PortDirection(direction),
        interface_path=f"{IF}/{interface}",
        init_values=(InitValueSpec(element="Torque", value=None),) if interface == "IfTorque" else (),
    )
    return waiting_entry(op, owner_team="Gamma", **more)


def _pend_connector(
    provider: str, provider_port: str, requester: str, requester_port: str, **more: object
) -> PendingEntry:
    op = AddConnector(
        id=(
            f"Connections:add_connector:{COMPOSITION}/{provider}.{provider_port}/"
            f"{requester}.{requester_port}"
        ),
        route=Route.HOLD,
        requester="bob",
        composition_path=COMPOSITION,
        name=f"{provider}_{provider_port}_{requester}_{requester_port}",
        provider_prototype=provider,
        provider_port=provider_port,
        requester_prototype=requester,
        requester_port=requester_port,
    )
    return waiting_entry(op, **more)


def _pend(op: AnyOperation, **more: object) -> PendingEntry:
    return waiting_entry(op, **more)


_M = f"{CT}/SwcM"
_BASE = {"route": Route.MODEL, "requester": "bob"}

ROW = [
    case("row: unknown sheet", [raw_row("Components", name="SwcA")], [R], (c.ROW_UNKNOWN_SHEET,)),
    case("row: free sheet name", [raw_row("Sheet1")], [R], (c.ROW_UNKNOWN_SHEET,)),
    case(
        "row: unknown action",
        [port_row("SwcA", "RpNew", action="REMOVE")],
        [R],
        (c.ROW_UNKNOWN_ACTION,),
    ),
    case("row: empty action", [port_row("SwcA", "RpNew", action="")], [R], (c.ROW_UNKNOWN_ACTION,)),
    case(
        "row: lower-case action is read",
        [port_row("SwcA", "RpNew", action=" add ")],
        [A],
        ok=(c.ROW_UNKNOWN_ACTION, c.ROW_UNKNOWN_SHEET, c.ROW_BLANK_CELL, c.ROW_ADD_WITH_ROWID),
    ),
    case("row: blank port name", [port_row("SwcA", "")], [R], (c.ROW_BLANK_CELL,)),
    case(
        "row: blank cells on every sheet",
        [
            type_row(""),
            interface_row("IfX", ""),
            element_row("", "E"),
            access_row("SwcA", "", "PpSpeed", "Speed", "implicit write"),
            trigger_row("SwcA", "RunA_10ms", ""),
            connection_row("SwcA", "", "SwcB", "RpSpeed"),
        ],
        [R] * 6,
        (c.ROW_BLANK_CELL,),
    ),
    case(
        "row: ADD on an exported row",
        [port_row("SwcA", "PpSpeed", "P", rid=port_rid("SwcA", "PpSpeed"))],
        [R],
        (c.ROW_ADD_WITH_ROWID,),
    ),
    case(
        "row: MODIFY and DELETE on new rows",
        [port_row("SwcA", "RpTorque", action="MODIFY"), port_row("SwcA", "RpTorque", action="DELETE")],
        [R, R],
        (c.ROW_CHANGE_WITHOUT_ROWID,),
    ),
    case(
        "row: stale RowID, object gone",
        [port_row("SwcA", "RpGone", action="DELETE", rid=port_rid("SwcA", "RpGone"))],
        [R],
        (c.ROW_STALE,),
    ),
    case(
        "row: delete an unused port",
        [port_row("SwcD", "PpUnused", "P", action="DELETE", rid=port_rid("SwcD", "PpUnused"))],
        [A],
        ok=(
            c.ROW_STALE,
            c.ROW_STALE_ROWID,
            c.ROW_UNSUPPORTED,
            c.ROW_IDENTITY_CHANGED,
            c.ROW_CHANGE_WITHOUT_ROWID,
            c.PORT_IN_USE,
            c.CFL_ADD_DELETE,
            c.CFL_DELETED_PORT,
        ),
    ),
    case(
        "row: outdated RowID, found by names",
        [port_row("SwcD", "PpUnused", "P", action="DELETE", rid="PT-000000000000")],
        [A],
        (c.ROW_STALE_ROWID,),
    ),
    case(
        "row: MODIFY/DELETE without an operation in v1",
        [
            type_row("uint8", action="DELETE", rid="DT-000000000000"),
            interface_row("IfSpeed", action="MODIFY", rid="IF-000000000000"),
            element_row("IfSpeed", "Speed", action="DELETE", rid="IE-000000000000"),
            access_row("SwcA", "RunA_10ms", "PpSpeed", "Speed", "implicit write", action="MODIFY", rid="RA-0"),
            trigger_row("SwcA", "RunA_10ms", "periodic", action="MODIFY", rid="RT-0"),
            connection_row("SwcA", "PpSpeed", "SwcB", "RpSpeed", action="MODIFY", rid="CN-0"),
        ],
        [R] * 6,
        (c.ROW_UNSUPPORTED,),
    ),
    case(
        "row: renamed in an exported row",
        [port_row("SwcD", "PpRenamed", "P", action="DELETE", rid=port_rid("SwcD", "PpUnused"))],
        [R],
        (c.ROW_IDENTITY_CHANGED,),
    ),
    case(
        "row: MODIFY that changes the direction",
        [port_row("SwcA", "RpTorque", "P", "IfTorque", action="MODIFY", rid=port_rid("SwcA", "RpTorque"))],
        [R],
        (c.ROW_IDENTITY_CHANGED,),
    ),
    case(
        "row: unreadable numbers and flags",
        [
            type_row("T1", base_type="uint8", lower_limit="abc"),
            type_row("T2", base_type="uint8", factor="0,5"),
            type_row("T3", "enumeration", base_type="uint8", enum_values="OFF;ON"),
            type_row("T4", "record", record_elements="a-uint8"),
            interface_row("IfBadFlag"),
            element_row("IfBadFlag", "X", queued="maybe"),
        ],
        [R] * 6,
        (c.ROW_BAD_CELL,),
    ),
    case(
        "row: numbers in several spellings",
        [type_row("T1", base_type="uint8", lower_limit="0x0A", upper_limit="200.0")],
        [A],
        ok=(c.ROW_BAD_CELL, c.DT_RANGE_FIT, c.DT_RANGE_ORDER),
    ),
]

NAME = [
    case(
        "name: bad syntax",
        [port_row("SwcA", "1Bad"), port_row("SwcA", "Rp-New"), port_row("SwcA", "Rp New")],
        [R, R, R],
        (c.NAME_SYNTAX,),
    ),
    case(
        "name: plain new port",
        [port_row("SwcA", "RpNew_1")],
        [A],
        ok=(
            c.NAME_SYNTAX,
            c.NAME_TOO_LONG,
            c.NAME_PATTERN,
            c.NAME_C_KEYWORD,
            c.NAME_DUPLICATE,
            c.NAME_CASE_CLASH,
            c.REF_UNKNOWN_SWC,
            c.REF_UNKNOWN_INTERFACE,
            c.OWN_LOCKED,
            c.OWN_SERVICE,
            c.OWN_COMPOSITION,
            c.PORT_DIRECTION,
            c.PORT_INIT_MISSING,
            c.PORT_INIT_PARSE,
            c.PORT_INIT_RANGE,
            c.CFL_MERGED,
            c.CFL_DIFFERENT,
            c.CFG_NAME_REGEX,
        ),
    ),
    case(
        "name: too long",
        [port_row("SwcA", "RpTooLongName")],
        [R],
        (c.NAME_TOO_LONG,),
        naming=naming(max_length=8),
    ),
    case(
        "name: at the length limit",
        [port_row("SwcA", "RpShort1")],
        [A],
        ok=(c.NAME_TOO_LONG,),
        naming=naming(max_length=8),
    ),
    case(
        "name: project pattern broken",
        [
            port_row("SwcA", "NewPort"),
            interface_row("Speedy"),
            element_row("Speedy", "V"),
            type_row("myType", base_type="uint8"),
            connection_row("SwcD", "PpSpeedD", "SwcC", "RpFree", name="conn1"),
        ],
        [R] * 5,
        (c.NAME_PATTERN,),
        naming=naming(port="(Pp|Rp)[A-Z]\\w*", interface="If[A-Z]\\w*", data_type="[A-Z]\\w*", connector="Asc_\\w+"),
    ),
    case(
        "name: project pattern kept",
        [
            port_row("SwcA", "RpNew"),
            interface_row("IfSpeedy"),
            element_row("IfSpeedy", "V"),
            type_row("MyType", base_type="uint8"),
            connection_row("SwcD", "PpSpeedD", "SwcC", "RpFree", name="Asc_conn1"),
        ],
        [A] * 5,
        ok=(c.NAME_PATTERN,),
        naming=naming(port="(Pp|Rp)[A-Z]\\w*", interface="If[A-Z]\\w*", data_type="[A-Z]\\w*", connector="Asc_\\w+"),
    ),
    case(
        "name: generated server runnable breaks the runnable pattern",
        [port_row("SwcA", "PpDiagNew", "P", "IfDiag", "")],
        [R],
        (c.NAME_PATTERN,),
        naming=naming(runnable="Run\\w+"),
    ),
    case("name: C keyword", [port_row("SwcA", "switch")], [R], (c.NAME_C_KEYWORD,)),
    case("name: keyword in another case", [port_row("SwcA", "Switch")], [A], ok=(c.NAME_C_KEYWORD,)),
    case(
        "name: port exists with another interface",
        [port_row("SwcA", "PpSpeed", "R", "IfTorque")],
        [R],
        (c.NAME_DUPLICATE,),
    ),
    case(
        "name: data type and interface exist with another definition",
        [type_row("uint8", base_type="sint8"), interface_row("IfSpeed", "client-server")],
        [R, R],
        (c.NAME_DUPLICATE,),
    ),
    case(
        "name: connector name taken in the composition",
        [connection_row("SwcD", "PpSpeedD", "SwcC", "RpFree", name="SwcA_PpSpeed_SwcB_RpSpeed")],
        [R],
        (c.NAME_DUPLICATE,),
    ),
    case(
        "name: port name equals the behavior name",
        [port_row("SwcA", "SwcA_InternalBehavior")],
        [R],
        (c.NAME_DUPLICATE,),
    ),
    case(
        "name: differs only by case from an existing port",
        [port_row("SwcA", "ppspeed")],
        [R],
        (c.NAME_CASE_CLASH,),
    ),
    case(
        "name: two new ports differ only by case",
        [port_row("SwcA", "RpNew"), port_row("SwcA", "rpnew")],
        [R, R],
        (c.NAME_CASE_CLASH,),
    ),
    case(
        "name: case clashes of types, interfaces and connectors",
        [
            type_row("UINT8", base_type="uint8"),
            interface_row("ifspeed"),
            element_row("ifspeed", "V"),
            connection_row("SwcD", "PpSpeedD", "SwcC", "RpFree", name="swca_ppspeed_swcb_rpspeed"),
        ],
        [R, R, R, R],
        (c.NAME_CASE_CLASH,),
    ),
    case(
        "name: same port name on two SWCs is no clash",
        [port_row("SwcA", "RpNew"), port_row("SwcB", "rpnew")],
        [A, A],
        ok=(c.NAME_CASE_CLASH, c.NAME_DUPLICATE),
    ),
]

REF = [
    case("ref: unknown SWC", [port_row("SwcNope", "RpNew")], [R], (c.REF_UNKNOWN_SWC,)),
    case("ref: ambiguous SWC name", [port_row("SwcDup", "RpNew")], [R], (c.REF_AMBIGUOUS_SWC,)),
    case(
        "ref: SWC by absolute path",
        [port_row("/Pkg1/SwcDup", "RpNew")],
        [A],
        ok=(c.REF_AMBIGUOUS_SWC, c.REF_UNKNOWN_SWC),
    ),
    case(
        "ref: unknown interface",
        [port_row("SwcA", "RpNew", interface="IfNope")],
        [R],
        (c.REF_UNKNOWN_INTERFACE,),
    ),
    case(
        "ref: ambiguous interface name",
        [port_row("SwcA", "RpNew", interface="IfDup")],
        [R],
        (c.REF_AMBIGUOUS_INTERFACE,),
    ),
    case(
        "ref: interface by absolute path",
        [port_row("SwcA", "RpNew", interface="/Other/IfDup")],
        [A],
        ok=(c.REF_AMBIGUOUS_INTERFACE, c.REF_UNKNOWN_INTERFACE),
    ),
    case(
        "ref: unknown data type",
        [interface_row("IfNew"), element_row("IfNew", "X", "NopeType")],
        [R, R],
        (c.REF_UNKNOWN_DATA_TYPE, c.IF_GROUP_REJECTED),
    ),
    case(
        "ref: ambiguous data type name",
        [interface_row("IfNew"), element_row("IfNew", "X", "Dup")],
        [R, R],
        (c.REF_AMBIGUOUS_DATA_TYPE,),
    ),
    case(
        "ref: data type by absolute path",
        [interface_row("IfNew"), element_row("IfNew", "X", "/Other/Dup")],
        [A, A],
        ok=(c.REF_AMBIGUOUS_DATA_TYPE, c.REF_UNKNOWN_DATA_TYPE, c.IF_GROUP_REJECTED, c.IF_EMPTY),
    ),
    case(
        "ref: unknown composition",
        [connection_row("SwcD", "PpSpeedD", "SwcC", "RpFree", composition="NoComp")],
        [R],
        (c.REF_UNKNOWN_COMPOSITION,),
    ),
    case(
        "ref: ambiguous composition name",
        [connection_row("DA", "PpSpeedD", "DB", "RpSpeedD", composition="DupComp")],
        [R],
        (c.REF_AMBIGUOUS_COMPOSITION,),
    ),
    case(
        "ref: composition by absolute path",
        [connection_row("DA", "PpSpeedC", "DB", "RpSpeedD", composition="/X/DupComp")],
        [A],
        ok=(c.REF_AMBIGUOUS_COMPOSITION, c.REF_UNKNOWN_COMPOSITION, c.REF_UNKNOWN_PROTOTYPE),
    ),
    case(
        "ref: unknown component",
        [
            connection_row("SwcZ", "PpX", "SwcA", "RpTorque"),
            connection_row("SwcE", "PpSpeedE", "SwcC", "RpFree", composition="EcuComposition"),
        ],
        [R, R],
        (c.REF_UNKNOWN_PROTOTYPE,),
    ),
    case(
        "ref: component whose SWC type is not loaded",
        [connection_row("Ghost", "PpX", "SwcC", "RpFree")],
        [R],
        (c.REF_UNKNOWN_SWC,),
    ),
    case(
        "ref: unknown port",
        [
            connection_row("SwcA", "PpNope", "SwcB", "RpSpeed"),
            access_row("SwcA", "RunA_10ms", "RpNope", "Speed", "implicit read"),
            trigger_row("SwcA", "RunA_10ms", "on data reception", "RpNope", "Speed"),
        ],
        [R, R, R],
        (c.REF_UNKNOWN_PORT,),
    ),
    case(
        "ref: unknown runnable",
        [
            access_row("SwcA", "RunNope", "PpSpeed", "Speed", "implicit write"),
            trigger_row("SwcA", "RunNope", "periodic", period_ms="10"),
            trigger_row("SwcC", "RunNope", "on operation invocation", "PpDiagAlt", "ReadDtc"),
        ],
        [R, R, R],
        (c.REF_UNKNOWN_RUNNABLE,),
    ),
    case(
        "ref: unknown element or operation",
        [
            access_row("SwcA", "RunA_10ms", "PpSpeed", "Nope", "implicit write"),
            access_row("SwcA", "RunA_10ms", "RpDiag", "Nope", "synchronous server call"),
            trigger_row("SwcC", "RunC", "on data reception", "RpMixed", "Nope", task="Task_10ms"),
            trigger_row("SwcC", "RunC_Plain", "on operation invocation", "PpDiagAlt", "Nope"),
        ],
        [R, R, R, R],
        (c.REF_UNKNOWN_TARGET,),
    ),
    case(
        "ref: access to known elements",
        [
            access_row("SwcA", "RunA_10ms", "RpTorque", "Torque", "implicit read"),
            access_row("SwcC", "RunC", "RpDiagC", "ReadDtc", "synchronous server call"),
        ],
        [A, A],
        ok=(
            c.REF_UNKNOWN_TARGET,
            c.REF_UNKNOWN_RUNNABLE,
            c.REF_UNKNOWN_PORT,
            c.REF_PORT_WITHOUT_INTERFACE,
            c.ACC_KIND,
            c.ACC_FIT,
            c.ACC_QUEUED,
            c.ACC_DUPLICATE,
        ),
    ),
    case(
        "ref: port on an interface whose request was rejected",
        [interface_row("IfEmpty"), port_row("SwcA", "RpE", interface="IfEmpty")],
        [R, R],
        (c.IF_EMPTY, c.REF_REJECTED_DEPENDENCY),
    ),
    case(
        "ref: port on an interface created further down",
        [
            port_row("SwcA", "RpE", interface="IfLater", init="5"),
            element_row("IfLater", "V", "LaterType"),
            interface_row("IfLater"),
            type_row("LaterType", base_type="uint8"),
        ],
        [A, A, A, A],
        ok=(c.REF_REJECTED_DEPENDENCY, c.REF_UNKNOWN_INTERFACE, c.REF_UNKNOWN_DATA_TYPE),
    ),
    case(
        "ref: port whose interface is not loaded",
        [
            access_row("SwcC", "RunC", "RpGhost", "X", "implicit read"),
            connection_row("SwcD", "PpSpeedD", "SwcC", "RpGhost"),
        ],
        [R, R],
        (c.REF_PORT_WITHOUT_INTERFACE,),
    ),
]

OWN = [
    case(
        "own: locked SWCs",
        [port_row("SwcL", "RpNew"), port_row("SwcLockedCfg", "RpNew")],
        [R, R],
        (c.OWN_LOCKED,),
    ),
    case(
        "own: locked SWC, delete and access",
        [
            port_row("SwcL", "PpLocked", "P", action="DELETE", rid=port_rid("SwcL", "PpLocked")),
            trigger_row("SwcL", "RunL", "periodic", period_ms="20"),
        ],
        [R, R],
        (c.OWN_LOCKED,),
    ),
    case("own: service SWC", [port_row("SwcSrv", "RpNew")], [R], (c.OWN_SERVICE,)),
    case("own: composition as SWC", [port_row("EcuComposition", "RpNew")], [R], (c.OWN_COMPOSITION,)),
    case(
        "own: connectors of a locked composition",
        [
            connection_row("LA", "PpWide", "LB", "RpSpeedCopy", composition="LockedComposition"),
            connection_row(
                "LA",
                "PpSpeedC",
                "LB",
                "RpSpeedD",
                composition="LockedComposition",
                action="DELETE",
                rid=connector_rid("LA_PpSpeedC_LB_RpSpeedD", LOCKED_COMPOSITION),
            ),
        ],
        [R, R],
        (c.OWN_COMPOSITION_LOCKED,),
    ),
    case(
        "own: model-owned SWC is routed, not rejected",
        [
            port_row("SwcM", "PpNew", "P", "IfTorque"),
            access_row("SwcM", "RunM", "RpSpeedM", "Speed", "implicit read"),
            trigger_row("SwcM", "RunM", "on data reception", "RpSpeedM", "Speed"),
            port_row("SwcM", "PpTorque", "P", "IfTorque", "3", action="MODIFY", rid=port_rid("SwcM", "PpTorque")),
        ],
        [W, W, W, W],
        ok=(c.OWN_LOCKED, c.OWN_SERVICE, c.OWN_COMPOSITION, c.CFL_PENDING, c.PEND_ALREADY_QUEUED),
    ),
    case(
        "own: connector to locked and service SWCs is allowed",
        [
            connection_row("SwcL", "PpLocked", "SwcC", "RpFree"),
            connection_row("SwcSrv", "PpService", "SwcM", "RpSpeedM"),
        ],
        [A, A],
        ok=(c.OWN_COMPOSITION_LOCKED, c.OWN_LOCKED, c.OWN_SERVICE),
    ),
]

DATA_TYPES = [
    case(
        "dt: value types",
        [
            type_row("NewU8", base_type="uint8", lower_limit="0", upper_limit="255"),
            type_row("NewS8", base_type="sint8", lower_limit="-128", upper_limit="127"),
            type_row("NewU16", base_type="uint16", lower_limit="0", upper_limit="65535"),
            type_row("NewF32", base_type="float32", lower_limit="-1.5", upper_limit="1e10"),
            type_row("NewBcd", base_type="bcd8", upper_limit="99999"),
            type_row("NewBool", "boolean", base_type="boolean"),
            type_row("NewApp", level="application", unit="%", factor="0.5", offset="-40"),
            type_row("NewScaled", base_type="uint8", factor="0.5", upper_limit="127.5", unit="Kmh"),
        ],
        [A] * 8,
        ok=(
            c.DT_LEVEL,
            c.DT_CATEGORY,
            c.DT_BASE_TYPE,
            c.DT_RANGE_ORDER,
            c.DT_RANGE_FIT,
            c.DT_CELL_NOT_USED,
            c.DT_UNIT,
            c.DT_SCALING,
        ),
    ),
    case("dt: unknown level", [type_row("T", level="system", base_type="uint8")], [R], (c.DT_LEVEL,)),
    case(
        "dt: category outside v1",
        [type_row("T1", "string"), type_row("T2", "blob"), type_row("T3", "type-reference")],
        [R, R, R],
        (c.DT_CATEGORY,),
    ),
    case(
        "dt: base type missing or not unique",
        [type_row("T1", base_type="uint7"), type_row("T2", base_type="dupbase")],
        [R, R],
        (c.DT_BASE_TYPE,),
    ),
    case(
        "dt: implementation type without base type",
        [type_row("T1")],
        [R],
        (c.ROW_BLANK_CELL,),
    ),
    case(
        "dt: lower limit above upper limit",
        [type_row("T1", base_type="uint8", lower_limit="10", upper_limit="5")],
        [R],
        (c.DT_RANGE_ORDER,),
    ),
    case(
        "dt: range does not fit the base type",
        [
            type_row("T1", base_type="uint8", upper_limit="300"),
            type_row("T2", base_type="uint8", lower_limit="-1"),
            type_row("T3", base_type="sint8", lower_limit="-129"),
            type_row("T4", base_type="uint16", upper_limit="70000"),
            type_row("T5", base_type="uint16", lower_limit="-1", upper_limit="65535"),
            type_row("T6", base_type="float32", upper_limit="1e39"),
            type_row("T7", base_type="uint8", factor="0.5", upper_limit="200"),
            type_row("T8", "enumeration", base_type="uint8", enum_values="0=A;300=B"),
            type_row("T9", "boolean", base_type="boolean", upper_limit="2"),
        ],
        [R] * 9,
        (c.DT_RANGE_FIT,),
    ),
    case(
        "dt: enumerations",
        [
            type_row("Gear", "enumeration", base_type="uint8", enum_values="0=P; 1=R ;2=N;3=D;"),
            type_row("DupValue", "enumeration", base_type="uint8", enum_values="0=A;0=B"),
            type_row("DupLabel", "enumeration", base_type="uint8", enum_values="0=A;1=A"),
            type_row("BadLabel", "enumeration", base_type="uint8", enum_values="0=1A"),
            type_row("NoValues", "enumeration", base_type="uint8"),
        ],
        [A, R, R, R, R],
        (c.DT_ENUM_VALUE_DUPLICATE, c.DT_ENUM_LABEL_DUPLICATE, c.NAME_SYNTAX, c.ROW_BLANK_CELL),
    ),
    case(
        "dt: enumeration with unique values and labels",
        [type_row("Gear", "enumeration", base_type="uint8", enum_values="0=P;1=R")],
        [A],
        ok=(c.DT_ENUM_VALUE_DUPLICATE, c.DT_ENUM_LABEL_DUPLICATE),
    ),
    case(
        "dt: arrays",
        [
            type_row("Arr4", "array", element_type="uint8", array_size="4"),
            type_row("Arr0", "array", element_type="uint8", array_size="0"),
            type_row("ArrNeg", "array", element_type="uint8", array_size="-3"),
            type_row("ArrFrac", "array", element_type="uint8", array_size="2.5"),
            type_row("ArrNoSize", "array", element_type="uint8"),
            type_row("ArrNoType", "array", element_type="NopeType", array_size="2"),
        ],
        [A, R, R, R, R, R],
        (c.DT_ARRAY_SIZE, c.ROW_BLANK_CELL, c.REF_UNKNOWN_DATA_TYPE),
    ),
    case(
        "dt: array with a positive size",
        [type_row("Arr4", "array", element_type="/DataTypes/uint8", array_size="4")],
        [A],
        ok=(c.DT_ARRAY_SIZE, c.DT_LEVEL_MIX, c.DT_CYCLE),
    ),
    case(
        "dt: records",
        [
            type_row("RecOk", "record", record_elements="a:uint8; b:uint16"),
            type_row("RecDup", "record", record_elements="a:uint8;a:uint16"),
            type_row("RecCase", "record", record_elements="a:uint8;A:uint16"),
            type_row("RecKeyword", "record", record_elements="int:uint8"),
        ],
        [A, R, R, R],
        (c.DT_RECORD_DUPLICATE, c.NAME_CASE_CLASH, c.NAME_C_KEYWORD),
        ok=(c.DT_CYCLE,),
    ),
    case(
        "dt: record with unique elements",
        [type_row("RecOk", "record", record_elements="a:uint8;b:SpeedArray")],
        [A],
        ok=(c.DT_RECORD_DUPLICATE,),
    ),
    case(
        "dt: circular records",
        [
            type_row("RecA", "record", record_elements="x:RecB"),
            type_row("RecB", "record", record_elements="y:ArrA"),
            type_row("ArrA", "array", element_type="RecA", array_size="2"),
            type_row("RecSelf", "record", record_elements="me:RecSelf"),
            type_row("RecUser", "record", record_elements="u:RecA"),
        ],
        [R, R, R, R, R],
        (c.DT_CYCLE, c.REF_REJECTED_DEPENDENCY),
    ),
    case(
        "dt: cells that do not belong to the category",
        [
            type_row("T1", "array", element_type="uint8", array_size="2", base_type="uint8"),
            type_row("T2", base_type="uint8", enum_values="0=A"),
            type_row("T3", "record", record_elements="a:uint8", unit="Kmh"),
            type_row("T4", "boolean", base_type="boolean", factor="2"),
        ],
        [R, R, R, R],
        (c.DT_CELL_NOT_USED,),
    ),
    case(
        "dt: unknown and ambiguous unit",
        [type_row("T1", base_type="uint8", unit="Furlong"), type_row("T2", base_type="uint8", unit="Dupunit")],
        [R, R],
        (c.DT_UNIT,),
    ),
    case("dt: factor zero", [type_row("T1", base_type="uint8", factor="0")], [R], (c.DT_SCALING,)),
    case(
        "dt: element type of another level",
        [
            type_row("RecMix", "record", record_elements="a:AppSpeed"),
            type_row("ArrMix", "array", level="application", element_type="uint8", array_size="2"),
            type_row("AppNew", level="application"),
            type_row("RecMix2", "record", record_elements="a:AppNew"),
        ],
        [R, R, A, R],
        (c.DT_LEVEL_MIX,),
    ),
    case(
        "dt: types the repo already has",
        [
            type_row("uint8", base_type="uint8"),
            type_row("EngineState", "enumeration", base_type="uint8", enum_values="0=OFF;1=RUN;2=FAULT"),
            type_row("SpeedArray", "array", element_type="uint16", array_size="4"),
            type_row("SpeedRec", "record", record_elements="a:uint16;b:uint8"),
            type_row("Percent", level="application", lower_limit="0", upper_limit="100", factor="0.5", unit="Pct"),
        ],
        [D, D, D, D, D],
        ok=(c.NAME_DUPLICATE,),
    ),
    case(
        "dt: existing type with another definition",
        [
            type_row("Limited", base_type="uint8", lower_limit="0", upper_limit="20"),
            type_row("SpeedArray", "array", element_type="uint16", array_size="8"),
            type_row("SpeedRec", "record", record_elements="a:uint16;c:uint8"),
            type_row("EngineState", "enumeration", base_type="uint8", enum_values="0=OFF;1=ON"),
        ],
        [R, R, R, R],
        (c.NAME_DUPLICATE,),
    ),
]

INTERFACES = [
    case(
        "if: sender-receiver interface",
        [
            interface_row("IfNew"),
            element_row("IfNew", "Value", "uint16"),
            element_row("IfNew", "Event", "uint8", queued="Y"),
        ],
        [A, A, A],
        ok=(
            c.IF_EMPTY,
            c.IF_KIND,
            c.IF_EXISTING,
            c.IF_ELEMENT_DUPLICATE,
            c.IF_CELL_NOT_USED,
            c.IF_GROUP_REJECTED,
        ),
    ),
    case(
        "if: client-server interface",
        [
            interface_row("IfSrv", "client-server"),
            element_row("IfSrv", "Reset", ""),
            element_row("IfSrv", "Read", "uint16", argument="Value", direction="out"),
            element_row("IfSrv", "Read", "uint8", argument="Index", direction="IN"),
            element_row("IfSrv", "Swap", "uint8", argument="Io", direction="INOUT"),
        ],
        [A] * 5,
        ok=(c.IF_ARGUMENT_DUPLICATE, c.IF_ARGUMENT_DIRECTION, c.IF_CELL_NOT_USED, c.IF_EMPTY),
    ),
    case("if: no elements", [interface_row("IfEmpty")], [R], (c.IF_EMPTY,)),
    case(
        "if: kinds that v1 cannot create",
        [
            interface_row("IfP", "parameter"),
            interface_row("IfN", "nv-data"),
            interface_row("IfT", "trigger"),
            interface_row("IfB", "bogus"),
            element_row("IfB", "X"),
        ],
        [R] * 5,
        (c.IF_KIND, c.IF_GROUP_REJECTED),
    ),
    case(
        "if: mode-switch with a known mode group",
        [
            interface_row("IfModeNew", "mode-switch"),
            element_row("IfModeNew", "mode", "EcuModes"),
            interface_row("IfModeNew2", "mode switch"),
            element_row("IfModeNew2", "mode", "/Modes/OtherModes"),
        ],
        [A, A, A, A],
        ok=(c.IF_MODE_GROUP, c.IF_KIND),
    ),
    case(
        "if: mode-switch without a resolvable mode group",
        [
            interface_row("IfModeA", "mode-switch"),
            element_row("IfModeA", "mode", "NoSuchGroup"),
            interface_row("IfModeB", "mode-switch"),
            element_row("IfModeB", "m1", "EcuModes"),
            element_row("IfModeB", "m2", "EcuModes"),
            interface_row("IfModeC", "mode-switch"),
            element_row("IfModeC", "mode", ""),
        ],
        [R] * 7,
        (c.IF_MODE_GROUP, c.ROW_BLANK_CELL),
    ),
    case(
        "if: element for an existing interface",
        [
            element_row("IfSpeed", "Extra", "uint8"),
            element_row("IfSpeed", "Speed", "uint8"),
            element_row("IfDiag", "Clear", ""),
            element_row("IfDiag", "ReadDtc", "uint16", argument="More", direction="IN"),
        ],
        [R, R, R, R],
        (c.IF_EXISTING,),
    ),
    case(
        "if: rows that an existing interface already fulfils",
        [
            interface_row("IfSpeed"),
            element_row("IfSpeed", "Speed", "uint16"),
            element_row("IfDiag", "ReadDtc", ""),
            element_row("IfDiag", "ReadDtc", "uint16", argument="Dtc", direction="OUT"),
            element_row("IfQueued", "Event", "uint8", queued="Y"),
            element_row("IfMode", "currentMode", "EcuModes"),
        ],
        [D] * 6,
        ok=(c.IF_EXISTING, c.NAME_DUPLICATE),
    ),
    case(
        "if: same element twice with different types",
        [interface_row("IfNew"), element_row("IfNew", "V", "uint8"), element_row("IfNew", "V", "uint16")],
        [R, R, R],
        (c.IF_ELEMENT_DUPLICATE, c.IF_GROUP_REJECTED),
    ),
    case(
        "if: same element twice, identical",
        [interface_row("IfNew"), element_row("IfNew", "V", "uint8"), element_row("IfNew", "V", "uint8")],
        [A, A, A],
        (c.CFL_MERGED,),
        ok=(c.IF_ELEMENT_DUPLICATE,),
    ),
    case(
        "if: same argument twice with different directions",
        [
            interface_row("IfSrv", "client-server"),
            element_row("IfSrv", "Read", "uint8", argument="X", direction="IN"),
            element_row("IfSrv", "Read", "uint8", argument="X", direction="OUT"),
        ],
        [R, R, R],
        (c.IF_ARGUMENT_DUPLICATE,),
    ),
    case(
        "if: bad argument direction",
        [
            interface_row("IfSrv", "client-server"),
            element_row("IfSrv", "Read", "uint8", argument="X", direction="SIDEWAYS"),
        ],
        [R, R],
        (c.IF_ARGUMENT_DIRECTION,),
    ),
    case(
        "if: cells of the other interface kind",
        [
            interface_row("IfNew"),
            element_row("IfNew", "V", "uint8", argument="X", direction="IN"),
            interface_row("IfSrv", "client-server"),
            element_row("IfSrv", "Read", "uint8", argument="X", direction="IN", queued="Y"),
        ],
        [R, R, R, R],
        (c.IF_CELL_NOT_USED,),
    ),
    case(
        "if: argument cells without an argument name",
        [interface_row("IfSrv", "client-server"), element_row("IfSrv", "Read", "uint8", direction="IN")],
        [R, R],
        (c.ROW_BLANK_CELL,),
    ),
    case(
        "if: element for an interface nobody creates",
        [element_row("IfNowhere", "V")],
        [R],
        (c.REF_UNKNOWN_INTERFACE,),
    ),
    case(
        "if: operation and argument names differ only by case",
        [
            interface_row("IfSrv", "client-server"),
            element_row("IfSrv", "Read", ""),
            element_row("IfSrv", "read", ""),
            interface_row("IfSrv2", "client-server"),
            element_row("IfSrv2", "Read", "uint8", argument="x", direction="IN"),
            element_row("IfSrv2", "Read", "uint8", argument="X", direction="IN"),
        ],
        [R] * 6,
        (c.NAME_CASE_CLASH,),
    ),
    case(
        "if: two workbooks define the interface with different kinds",
        [
            interface_row("IfNew"),
            element_row("IfNew", "V", "uint8"),
            interface_row("IfNew", "client-server", file=BETA),
        ],
        [R, R, R],
        (c.CFL_DIFFERENT,),
    ),
    case(
        "if: two workbooks define the interface with different elements",
        [
            interface_row("IfNew"),
            element_row("IfNew", "V", "uint8"),
            interface_row("IfNew", file=BETA),
            element_row("IfNew", "W", "uint8", file=BETA),
        ],
        [R, R, R, R],
        (c.CFL_DIFFERENT,),
    ),
    case(
        "if: two workbooks define the same interface",
        [
            interface_row("IfNew"),
            element_row("IfNew", "V", "uint8"),
            interface_row("IfNew", file=BETA),
            element_row("IfNew", "V", "uint8", file=BETA),
        ],
        [A, A, A, A],
        (c.CFL_MERGED,),
        ok=(c.CFL_DIFFERENT,),
    ),
]

PORTS = [
    case("port: bad direction", [port_row("SwcA", "RpNew", "X")], [R], (c.PORT_DIRECTION,)),
    case(
        "port: PR on a client-server or mode interface",
        [port_row("SwcA", "PrDiag", "PR", "IfDiag", ""), port_row("SwcA", "PrMode", "PR", "IfMode", "")],
        [R, R],
        (c.PORT_PR_KIND,),
    ),
    case(
        "port: PR on sender-receiver style interfaces",
        [port_row("SwcA", "PrNew", "pr", "IfSpeed"), port_row("SwcA", "PrNv", "PR", "IfNv", "")],
        [A, A],
        ok=(c.PORT_PR_KIND, c.PORT_DIRECTION),
    ),
    case(
        "port: init value missing",
        [
            port_row("SwcA", "RpNew", init=""),
            port_row("SwcA", "PpNew", "P", init=""),
            port_row("SwcA", "RpMultiNew", "R", "IfMulti", "A=1"),
        ],
        [R, R, R],
        (c.PORT_INIT_MISSING,),
    ),
    case(
        "port: init values and explicit none",
        [
            port_row("SwcA", "RpM1", "R", "IfMulti", "A=1; B=none"),
            port_row("SwcA", "RpM2", "R", "IfMulti", "NONE"),
            port_row("SwcA", "RpQ", "R", "IfQueued", ""),
            port_row("SwcA", "RpMx", "R", "IfMixed", "Value=1"),
            port_row("SwcA", "RpMx2", "R", "IfMixed", "7"),
            port_row("SwcA", "RpDg", "R", "IfDiag", ""),
            port_row("SwcA", "RpPar", "R", "IfParam", ""),
        ],
        [A, A, A, A, R, A, A],
        (c.PORT_INIT_FORMAT,),
        ok=(c.PORT_INIT_MISSING, c.PORT_INIT_NOT_USED),
    ),
    case(
        "port: init cell does not match the elements",
        [
            port_row("SwcA", "RpM1", "R", "IfMulti", "5"),
            port_row("SwcA", "RpM2", "R", "IfMulti", "Z=1;A=1;B=2"),
            port_row("SwcA", "RpM3", "R", "IfMulti", "A=1;A=2;B=1"),
            port_row("SwcA", "RpM4", "R", "IfMulti", "1;2"),
            port_row("SwcA", "RpM5", "R", "IfMulti", "A=;B=1"),
        ],
        [R] * 5,
        (c.PORT_INIT_FORMAT,),
    ),
    case(
        "port: init cell matches the elements",
        [port_row("SwcA", "RpM1", "R", "IfMulti", "B=2;A=1")],
        [A],
        ok=(c.PORT_INIT_FORMAT,),
    ),
    case(
        "port: init value is not a value of the type",
        [
            port_row("SwcA", "Rp1", "R", "IfSpeed", "fast lane"),
            port_row("SwcA", "Rp2", "R", "IfSpeed8", "1.5"),
            port_row("SwcA", "Rp3", "R", "IfBool", "2"),
            port_row("SwcA", "Rp4", "R", "IfState", "IDLE"),
            port_row("SwcA", "Rp5", "R", "IfState", "7"),
            port_row("SwcA", "Rp6", "R", "IfSpeed8", "abc"),
            port_row("SwcA", "Rp7", "R", "IfUnknownType", "5 apples"),
        ],
        [R] * 7,
        (c.PORT_INIT_PARSE,),
    ),
    case(
        "port: init values of every scalar kind",
        [
            port_row("SwcA", "Rp1", "R", "IfState", "RUN"),
            port_row("SwcA", "Rp2", "R", "IfState", "1"),
            port_row("SwcA", "Rp3", "R", "IfBool", "TRUE"),
            port_row("SwcA", "Rp4", "R", "IfFloat", "1.5"),
            port_row("SwcA", "Rp5", "R", "IfPercent", "12.5"),
            port_row("SwcA", "Rp6", "R", "IfRef", "200"),
            port_row("SwcA", "Rp7", "R", "IfUnknownType", "SOME_CONSTANT"),
            port_row("SwcA", "Rp8", "R", "IfSpeed", "0x10"),
            port_row("SwcA", "Rp9", "R", "IfLimited", "15"),
            port_row("SwcA", "Rp10", "R", "IfSigned", "-128"),
        ],
        [A] * 10,
        ok=(c.PORT_INIT_PARSE, c.PORT_INIT_RANGE, c.PORT_INIT_COMPOSITE),
    ),
    case(
        "port: init value out of range",
        [
            port_row("SwcA", "Rp1", "R", "IfSpeed8", "256"),
            port_row("SwcA", "Rp2", "R", "IfLimited", "5"),
            port_row("SwcA", "Rp3", "R", "IfPercent", "101"),
            port_row("SwcA", "Rp4", "R", "IfSigned", "-129"),
            port_row("SwcA", "Rp5", "R", "IfSpeed", "70000"),
            port_row("SwcA", "Rp6", "R", "IfRef", "300"),
        ],
        [R] * 6,
        (c.PORT_INIT_RANGE,),
    ),
    case(
        "port: init value where none can be stored",
        [
            port_row("SwcA", "Rp1", "R", "IfDiag", "0"),
            port_row("SwcA", "Rp2", "R", "IfQueued", "5"),
            port_row("SwcA", "Rp3", "R", "IfMixed", "Value=1;Evt=2"),
            port_row("SwcA", "Rp4", "R", "IfMode", "none"),
        ],
        [R] * 4,
        (c.PORT_INIT_NOT_USED,),
    ),
    case(
        "port: init value of an array type",
        [port_row("SwcA", "Rp1", "R", "IfArr", "{1,2,3,4}"), port_row("SwcA", "Rp2", "R", "IfArr", "none")],
        [R, A],
        (c.PORT_INIT_COMPOSITE,),
    ),
    case(
        "port: delete a port that is in use",
        [port_row("SwcA", "PpSpeed", "P", action="DELETE", rid=port_rid("SwcA", "PpSpeed"))],
        [R],
        (c.PORT_IN_USE,),
    ),
    case(
        "port: delete a port together with everything on it",
        [
            port_row("SwcB", "RpSpeed", action="DELETE", rid=port_rid("SwcB", "RpSpeed")),
            connection_row(
                "SwcA",
                "PpSpeed",
                "SwcB",
                "RpSpeed",
                action="DELETE",
                rid=connector_rid("SwcA_PpSpeed_SwcB_RpSpeed"),
            ),
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
        ],
        [A, A, A, A],
        ok=(c.PORT_IN_USE, c.TRG_SERVER_ORPHAN, c.ROW_STALE),
    ),
    case(
        "port: MODIFY to an interface that breaks the use",
        [port_row("SwcA", "PpSpeed", "P", "IfTorque", "0", action="MODIFY", rid=port_rid("SwcA", "PpSpeed"))],
        [R],
        (c.PORT_MODIFY_BREAKS,),
    ),
    case(
        "port: MODIFY to a compatible interface",
        [
            port_row(
                "SwcA",
                "PpSpeed",
                "P",
                "IfSpeedWide",
                "Speed=0;Extra=0",
                action="MODIFY",
                rid=port_rid("SwcA", "PpSpeed"),
            )
        ],
        [A],
        ok=(c.PORT_MODIFY_BREAKS, c.ROW_IDENTITY_CHANGED),
    ),
    case(
        "port: MODIFY of the init value only, and without any change",
        [
            port_row("SwcA", "RpTorque", "R", "IfTorque", "5", action="MODIFY", rid=port_rid("SwcA", "RpTorque")),
            port_row("SwcB", "RpSpeed", "R", "IfSpeed", "0", action="MODIFY", rid=port_rid("SwcB", "RpSpeed")),
        ],
        [A, D],
    ),
    case(
        "port: exists as requested, other init value",
        [port_row("SwcA", "PpSpeed", "P", "IfSpeed", "5")],
        [D],
        (c.PORT_INIT_DIFFERS,),
    ),
    case(
        "port: exists as requested",
        [port_row("SwcA", "PpSpeed", "P", "IfSpeed", "0"), port_row("SwcA", "RpDiag", "R", "IfDiag", "")],
        [D, D],
        ok=(c.PORT_INIT_DIFFERS, c.NAME_DUPLICATE),
    ),
]

CONNECTORS = [
    case(
        "con: plain connection with default and explicit name",
        [
            connection_row("SwcD", "PpSpeedD", "SwcC", "RpFree"),
            connection_row("SwcC", "PpWide", "SwcD", "RpSpeedCopy", name="Asc_Wide"),
            connection_row("SwcC", "PrSpeed", "SwcM", "RpSpeedM", composition=COMPOSITION),
        ],
        [A, A, A],
        ok=(
            c.CON_SIDE,
            c.CON_SELF,
            c.CON_INCOMPATIBLE,
            c.CON_DUPLICATE,
            c.CON_NO_COMPOSITION,
            c.CON_MANY_COMPOSITIONS,
            c.MUL_N_TO_M,
            c.MUL_N_TO_ONE,
            c.CFG_NAME_PATTERN,
            c.REF_UNKNOWN_COMPOSITION,
        ),
    ),
    case(
        "con: provider and requester swapped",
        [connection_row("SwcB", "RpSpeed", "SwcA", "PpSpeed"), connection_row("SwcD", "PpSpeedD", "SwcC", "PpSpeedC")],
        [R, R],
        (c.CON_SIDE,),
    ),
    case("con: port to itself", [connection_row("SwcC", "PrSpeed", "SwcC", "PrSpeed")], [R], (c.CON_SELF,)),
    case(
        "con: incompatible interfaces",
        [
            connection_row("SwcC", "PpSpeed8", "SwcC", "RpFree"),
            connection_row("SwcC", "PpMode", "SwcC", "RpFree"),
            connection_row("SwcC", "PpDiagAlt", "SwcC", "RpDiagC"),
            connection_row("SwcC", "PpMode", "SwcD", "RpModeOther"),
            connection_row("SwcD", "PpSpeedD", "SwcD", "RpSpeedQ"),
            connection_row("SwcD", "PpSpeedD", "SwcA", "RpTorque"),
            connection_row("SwcC", "PpDiagC", "SwcD", "RpDiag2D"),
        ],
        [R] * 7,
        (c.CON_INCOMPATIBLE,),
    ),
    case(
        "con: already connected, other connector name asked",
        [connection_row("SwcA", "PpSpeed", "SwcB", "RpSpeed", name="Asc_Second")],
        [R],
        (c.CON_DUPLICATE,),
    ),
    case(
        "con: already connected",
        [
            connection_row("SwcA", "PpSpeed", "SwcB", "RpSpeed"),
            connection_row("SwcB", "PpDiag", "SwcA", "RpDiag", name="SwcB_PpDiag_SwcA_RpDiag"),
        ],
        [D, D],
        ok=(c.CON_DUPLICATE,),
    ),
    case(
        "con: components in different compositions",
        [connection_row("SwcE", "PpSpeedE", "SwcC", "RpFree")],
        [R],
        (c.CON_NO_COMPOSITION,),
    ),
    case(
        "con: components in two compositions",
        [connection_row("ProtoX", "PpSpeedC", "ProtoY", "RpSpeedD")],
        [R],
        (c.CON_MANY_COMPOSITIONS,),
    ),
    case(
        "con: composition named in the row",
        [connection_row("ProtoX", "PpSpeedC", "ProtoY", "RpSpeedD", composition="SubComposition")],
        [A],
        ok=(c.CON_MANY_COMPOSITIONS, c.CON_NO_COMPOSITION),
    ),
    case(
        "mul: n:m sender-receiver",
        [connection_row("SwcC", "PpSpeedC", "SwcB", "RpSpeed")],
        [R],
        (c.MUL_N_TO_M,),
    ),
    case(
        "mul: n:m built by two rows of one import",
        [connection_row("SwcD", "PpSpeedD", "SwcB", "RpSpeed"), connection_row("SwcD", "PpSpeedD", "SwcC", "RpFree")],
        [A, R],
        (c.MUL_N_TO_M, c.MUL_N_TO_ONE),
    ),
    case(
        "mul: 1:n sender-receiver",
        [connection_row("SwcA", "PpSpeed", "SwcC", "RpFree"), connection_row("SwcA", "PpSpeed", "SwcM", "RpSpeedM")],
        [A, A],
        ok=(c.MUL_N_TO_M, c.MUL_N_TO_ONE),
    ),
    case(
        "mul: n:1 sender-receiver is a warning",
        [connection_row("SwcD", "PpSpeedD", "SwcB", "RpSpeed")],
        [A],
        (c.MUL_N_TO_ONE,),
        ok=(c.MUL_N_TO_M,),
    ),
    case(
        "mul: client with a second server",
        [connection_row("SwcC", "PpDiagC", "SwcA", "RpDiag")],
        [R],
        (c.MUL_CLIENT,),
    ),
    case(
        "mul: server with many clients",
        [connection_row("SwcB", "PpDiag", "SwcC", "RpDiagC"), connection_row("SwcB", "PpDiag", "SwcD", "RpDiagD")],
        [A, A],
        ok=(c.MUL_CLIENT,),
    ),
    case(
        "mul: mode user with a second mode manager",
        [connection_row("SwcD", "PpMode2", "SwcD", "RpMode")],
        [R],
        (c.MUL_SINGLE_PROVIDER,),
    ),
    case(
        "mul: mode manager with many users",
        [connection_row("SwcC", "PpMode", "SwcC", "RpModeC")],
        [A],
        ok=(c.MUL_SINGLE_PROVIDER,),
    ),
    case(
        "mul: deleting the old connector frees the client",
        [
            connection_row("SwcC", "PpDiagC", "SwcA", "RpDiag"),
            connection_row(
                "SwcB", "PpDiag", "SwcA", "RpDiag", action="DELETE", rid=connector_rid("SwcB_PpDiag_SwcA_RpDiag")
            ),
        ],
        [A, A],
        ok=(c.MUL_CLIENT,),
    ),
]

ACCESS = [
    case(
        "acc: unknown and unsupported kinds",
        [
            access_row("SwcA", "RunA_10ms", "PpSpeed", "Speed", "teleport"),
            access_row("SwcC", "RunC", "RpParam", "Gain", "parameter access"),
        ],
        [R, R],
        (c.ACC_KIND,),
    ),
    case(
        "acc: kind does not fit the port",
        [
            access_row("SwcA", "RunA_10ms", "PpSpeed", "Speed", "implicit read"),
            access_row("SwcA", "RunA_10ms", "RpTorque", "Torque", "implicit write"),
            access_row("SwcA", "RunA_10ms", "RpTorque", "Torque", "synchronous server call"),
            access_row("SwcB", "RunB_OnSpeed", "PpDiag", "ReadDtc", "synchronous server call"),
            access_row("SwcD", "RunD", "RpMode", "currentMode", "mode switch"),
            access_row("SwcA", "RunA_10ms", "RpDiag", "ReadDtc", "explicit read"),
            access_row("SwcA", "RunA_10ms", "RpTorque", "Torque", "mode access"),
        ],
        [R] * 7,
        (c.ACC_FIT,),
    ),
    case(
        "acc: every kind on a fitting port",
        [
            access_row("SwcC", "RunC", "PrSpeed", "Speed", "explicit read"),
            access_row("SwcC", "RunC", "PrSpeed", "Speed", "implicit write"),
            access_row("SwcD", "RunD", "RpMode", "currentMode", "mode access"),
            access_row("SwcC", "RunC", "PpMode", "currentMode", "mode switch"),
            access_row("SwcC", "RunC", "RpDiagC", "ReadDtc", "asynchronous server call"),
            access_row("SwcC", "RunC", "RpNv", "Stored", "implicit read"),
            access_row("SwcD", "RunD", "RpModeOther", "anyMode", "MODE_ACCESS"),
            access_row("SwcD", "RunD", "PpSpeedD", "Speed", "explicit send"),
        ],
        [A, R, A, A, A, A, A, A],
        (c.ACC_DUPLICATE,),
        ok=(c.ACC_FIT, c.ACC_KIND),
    ),
    case(
        "acc: queued and non-queued elements",
        [
            access_row("SwcA", "RunA_10ms", "RpTorque", "Torque", "queued receive"),
            access_row("SwcC", "RunC", "RpQueuedC", "Event", "implicit read"),
            access_row("SwcC", "RunC", "RpQueuedC", "Event", "explicit read"),
            access_row("SwcD", "RunD", "PpQueued", "Event", "implicit write"),
        ],
        [R, R, R, R],
        (c.ACC_QUEUED,),
    ),
    case(
        "acc: queued receive and send",
        [
            access_row("SwcC", "RunC", "RpQueuedC", "Event", "queued receive"),
            access_row("SwcD", "RunD", "PpQueued", "Event", "explicit send"),
            access_row("SwcC", "RunC", "RpMixed", "Evt", "queued receive"),
        ],
        [A, A, A],
        ok=(c.ACC_QUEUED,),
    ),
    case(
        "acc: second kind of read for the same element",
        [access_row("SwcC", "RunC", "RpMulti", "A", "explicit read")],
        [R],
        (c.ACC_DUPLICATE,),
    ),
    case(
        "acc: existing access and a new element",
        [
            access_row("SwcC", "RunC", "RpMulti", "A", "implicit read"),
            access_row("SwcC", "RunC", "RpMulti", "B", "implicit read"),
        ],
        [D, A],
        ok=(c.ACC_DUPLICATE,),
    ),
    case(
        "acc: replace an access kind by delete and add",
        [
            access_row("SwcC", "RunC", "RpMulti", "A", "explicit read"),
            access_row(
                "SwcC",
                "RunC",
                "RpMulti",
                "A",
                "implicit read",
                action="DELETE",
                rid=access_rid("SwcC", "RunC", "DRA_RpMulti_A"),
            ),
        ],
        [A, A],
        ok=(c.ACC_DUPLICATE, c.CFL_ADD_DELETE),
    ),
    case(
        "acc: two rows ask for different reads of one element",
        [
            access_row("SwcA", "RunA_10ms", "RpTorque", "Torque", "implicit read"),
            access_row("SwcA", "RunA_10ms", "RpTorque", "Torque", "explicit read", file=BETA),
        ],
        [R, R],
        (c.CFL_DIFFERENT,),
    ),
    case(
        "acc: access on a new port and a new server runnable",
        [
            port_row("SwcA", "RpNew"),
            access_row("SwcA", "RunA_10ms", "RpNew", "Speed", "implicit read"),
            port_row("SwcA", "PpDiagNew", "P", "IfDiag", ""),
            access_row("SwcA", "PpDiagNew_ReadDtc", "RpTorque", "Torque", "explicit read"),
        ],
        [A, A, A, A],
        ok=(c.REF_UNKNOWN_PORT, c.REF_UNKNOWN_RUNNABLE),
    ),
]

TRIGGERS = [
    case(
        "trg: unknown and unsupported kinds",
        [
            trigger_row("SwcA", "RunA_10ms", "sometimes"),
            trigger_row("SwcA", "RunA_10ms", "on data send completion", "PpSpeed", "Speed"),
        ],
        [R, R],
        (c.TRG_KIND,),
    ),
    case(
        "trg: periodic without a positive period",
        [
            trigger_row("SwcA", "RunA_10ms", "periodic"),
            trigger_row("SwcA", "RunA_10ms", "periodic", period_ms="0"),
            trigger_row("SwcA", "RunA_10ms", "periodic", period_ms="-5"),
            trigger_row("SwcA", "RunA_10ms", "periodic", period_ms="fast"),
        ],
        [R] * 4,
        (c.TRG_PERIOD,),
    ),
    case(
        "trg: cells that do not belong to the kind",
        [
            trigger_row("SwcA", "RunA_10ms", "periodic", "RpTorque", "Torque", period_ms="5"),
            trigger_row("SwcA", "RunA_10ms", "on data reception", "RpTorque", "Torque", period_ms="5"),
            trigger_row("SwcA", "RunA_10ms", "init", "RpTorque"),
            trigger_row("SwcA", "RunA_10ms", "background", period_ms="5"),
        ],
        [R] * 4,
        (c.TRG_CELL_NOT_USED,),
    ),
    case(
        "trg: kind does not fit the port",
        [
            trigger_row("SwcA", "RunA_10ms", "on data reception", "PpSpeed", "Speed"),
            trigger_row("SwcA", "RunA_10ms", "on operation invocation", "RpDiag", "ReadDtc"),
            trigger_row("SwcC", "RunC_Plain", "on mode switch", "PpMode", "RUN"),
            trigger_row("SwcA", "RunA_10ms", "on data reception error", "RpDiag", "ReadDtc"),
            trigger_row("SwcC", "RunC_Plain", "on data reception", "RpNv", "Stored"),
        ],
        [R] * 5,
        (c.TRG_FIT,),
    ),
    case(
        "trg: every kind on a fitting port",
        [
            trigger_row("SwcA", "RunA_10ms", "on data reception", "RpTorque", "Torque"),
            trigger_row("SwcA", "RunA_10ms", "on data reception error", "RpTorque", "Torque"),
            trigger_row("SwcD", "RunD", "on mode switch", "RpMode", "RUN"),
            trigger_row("SwcA", "RunA_10ms", "periodic", period_ms="2.5"),
            trigger_row("SwcC", "RunC_Plain", "init"),
            trigger_row("SwcC", "RunC_Plain", "background", task="Task_Init"),
            trigger_row("SwcC", "RunC_Plain", "on operation invocation", "PpDiagAlt", "ReadDtc"),
        ],
        [A] * 7,
        ok=(
            c.TRG_KIND,
            c.TRG_PERIOD,
            c.TRG_CELL_NOT_USED,
            c.TRG_FIT,
            c.TRG_TASK,
            c.TRG_SERVER_TAKEN,
            c.TRG_TASK_AMBIGUOUS,
        ),
    ),
    case(
        "trg: unknown task",
        [trigger_row("SwcA", "RunA_10ms", "periodic", period_ms="20", task="Task_Nope")],
        [R],
        (c.TRG_TASK,),
    ),
    case(
        "trg: server operation already has a runnable",
        [trigger_row("SwcB", "RunB_OnSpeed", "on operation invocation", "PpDiag", "ReadDtc")],
        [R],
        (c.TRG_SERVER_TAKEN,),
    ),
    case(
        "trg: server operation already served by this runnable",
        [trigger_row("SwcB", "PpDiag_ReadDtc", "on operation invocation", "PpDiag", "ReadDtc")],
        [D],
        ok=(c.TRG_SERVER_TAKEN, c.TRG_TASK_DIFFERS),
    ),
    case(
        "trg: runnable in two tasks, no task named",
        [trigger_row("SwcC", "RunC", "periodic", period_ms="50")],
        [R],
        (c.TRG_TASK_AMBIGUOUS,),
    ),
    case(
        "trg: runnable in two tasks, task named",
        [trigger_row("SwcC", "RunC", "periodic", period_ms="50", task="Task_Event")],
        [A],
        ok=(c.TRG_TASK_AMBIGUOUS, c.TRG_TASK),
    ),
    case(
        "trg: deleting the only trigger of a server operation",
        [
            trigger_row(
                "SwcB",
                "PpDiag_ReadDtc",
                "on operation invocation",
                "PpDiag",
                "ReadDtc",
                action="DELETE",
                rid=trigger_rid("SwcB", "OIT_PpDiag_ReadDtc"),
            )
        ],
        [R],
        (c.TRG_SERVER_ORPHAN,),
    ),
    case(
        "trg: replacing the runnable of a server operation",
        [
            trigger_row(
                "SwcB",
                "PpDiag_ReadDtc",
                "on operation invocation",
                "PpDiag",
                "ReadDtc",
                action="DELETE",
                rid=trigger_rid("SwcB", "OIT_PpDiag_ReadDtc"),
            ),
            trigger_row("SwcB", "RunB_OnSpeed", "on operation invocation", "PpDiag", "ReadDtc"),
            trigger_row(
                "SwcA", "RunA_10ms", "periodic", period_ms="10", action="DELETE", rid=trigger_rid("SwcA", "TMT_RunA_10ms")
            ),
        ],
        [A, A, A],
        ok=(c.TRG_SERVER_ORPHAN, c.TRG_SERVER_TAKEN),
    ),
    case(
        "trg: exists, but mapped to another task",
        [trigger_row("SwcA", "RunA_10ms", "periodic", period_ms="10", task="Task_Init")],
        [D],
        (c.TRG_TASK_DIFFERS,),
    ),
    case(
        "trg: exists as requested",
        [
            trigger_row("SwcA", "RunA_10ms", "periodic", period_ms="10"),
            trigger_row("SwcB", "RunB_OnSpeed", "on data reception", "RpSpeed", "Speed", task="Task_Event"),
        ],
        [D, D],
        ok=(c.TRG_TASK_DIFFERS,),
    ),
    case(
        "trg: add and delete of the same trigger",
        [
            trigger_row("SwcA", "RunA_10ms", "periodic", period_ms="10"),
            trigger_row(
                "SwcA", "RunA_10ms", "periodic", period_ms="10", action="DELETE", rid=trigger_rid("SwcA", "TMT_RunA_10ms")
            ),
        ],
        [R, R],
        (c.CFL_ADD_DELETE,),
    ),
    case(
        "trg: two rows give one server operation different runnables",
        [
            port_row("SwcA", "PpDiagNew", "P", "IfDiag", ""),
            trigger_row("SwcA", "RunOne", "on operation invocation", "PpDiagNew", "ReadDtc"),
            trigger_row("SwcA", "RunTwo", "on operation invocation", "PpDiagNew", "ReadDtc", file=BETA),
        ],
        [R, R, R],
        (c.CFL_DIFFERENT, c.REF_REJECTED_DEPENDENCY),
    ),
    case(
        "trg: one new runnable name for two operations",
        [
            port_row("SwcA", "PpDiagNew", "P", "IfDiag2", ""),
            trigger_row("SwcA", "RunBoth", "on operation invocation", "PpDiagNew", "ReadDtc"),
            trigger_row("SwcA", "RunBoth", "on operation invocation", "PpDiagNew", "Clear"),
        ],
        [R, R, R],
        (c.NAME_DUPLICATE,),
    ),
]

CONFLICTS = [
    case(
        "cfl: same request in two workbooks",
        [
            port_row("SwcA", "RpNew"),
            port_row("SwcA", "RpNew", file=BETA, requester="bob"),
            connection_row("SwcD", "PpSpeedD", "SwcC", "RpFree"),
            connection_row(
                "SwcD", "PpSpeedD", "SwcC", "RpFree", name="SwcD_PpSpeedD_SwcC_RpFree", file=BETA
            ),
            type_row("NewT", base_type="uint8"),
            type_row("NewT", base_type="uint8", file=BETA),
            trigger_row("SwcA", "RunA_10ms", "periodic", period_ms="20"),
            trigger_row("SwcA", "RunA_10ms", "periodic", period_ms="20.0", file=BETA),
            access_row("SwcA", "RunA_10ms", "RpTorque", "Torque", "implicit read"),
            access_row("SwcA", "RunA_10ms", "RpTorque", "Torque", "IMPLICIT_READ", file=BETA),
        ],
        [A] * 10,
        (c.CFL_MERGED,),
        ok=(c.CFL_DIFFERENT,),
    ),
    case(
        "cfl: same port name, different interfaces",
        [port_row("SwcA", "RpNew"), port_row("SwcA", "RpNew", "R", "IfTorque", file=BETA)],
        [R, R],
        (c.CFL_DIFFERENT,),
    ),
    case(
        "cfl: same object requested differently on other sheets",
        [
            connection_row("SwcD", "PpSpeedD", "SwcC", "RpFree"),
            connection_row("SwcD", "PpSpeedD", "SwcC", "RpFree", name="Asc_Other", file=BETA),
            type_row("NewT", base_type="uint8"),
            type_row("NewT", base_type="sint8", file=BETA),
            trigger_row("SwcA", "RunA_10ms", "periodic", period_ms="20", task="Task_Init"),
            trigger_row("SwcA", "RunA_10ms", "periodic", period_ms="20", task="Task_Event", file=BETA),
        ],
        [R] * 6,
        (c.CFL_DIFFERENT,),
    ),
    case(
        "cfl: add and delete of one port",
        [
            port_row("SwcD", "PpUnused", "P"),
            port_row("SwcD", "PpUnused", "P", action="DELETE", rid=port_rid("SwcD", "PpUnused")),
        ],
        [R, R],
        (c.CFL_ADD_DELETE,),
    ),
    case(
        "cfl: add and delete of one connector, modify and delete of one port",
        [
            connection_row("SwcA", "PpSpeed", "SwcB", "RpSpeed"),
            connection_row(
                "SwcA", "PpSpeed", "SwcB", "RpSpeed", action="DELETE", rid=connector_rid("SwcA_PpSpeed_SwcB_RpSpeed")
            ),
            port_row("SwcD", "PpUnused", "P", init="4", action="MODIFY", rid=port_rid("SwcD", "PpUnused")),
            port_row("SwcD", "PpUnused", "P", action="DELETE", rid=port_rid("SwcD", "PpUnused")),
            access_row("SwcC", "RunC", "RpMulti", "A", "implicit read"),
            access_row(
                "SwcC",
                "RunC",
                "RpMulti",
                "A",
                "implicit read",
                action="DELETE",
                rid=access_rid("SwcC", "RunC", "DRA_RpMulti_A"),
            ),
        ],
        [R] * 6,
        (c.CFL_ADD_DELETE,),
    ),
    case(
        "cfl: connector, access and trigger on a port another row deletes",
        [
            port_row("SwcD", "PpUnused", "P", action="DELETE", rid=port_rid("SwcD", "PpUnused")),
            connection_row("SwcD", "PpUnused", "SwcC", "RpFree"),
            access_row("SwcD", "RunD", "PpUnused", "Speed", "implicit write"),
        ],
        [R, R, R],
        (c.CFL_DELETED_PORT,),
    ),
    case(
        "cfl: the same delete in two workbooks",
        [
            port_row("SwcD", "PpUnused", "P", action="DELETE", rid=port_rid("SwcD", "PpUnused")),
            port_row("SwcD", "PpUnused", "P", action="DELETE", rid=port_rid("SwcD", "PpUnused"), file=BETA),
        ],
        [A, A],
        (c.CFL_MERGED,),
    ),
    case(
        "cfl: two different MODIFY rows for one port",
        [
            port_row("SwcA", "RpTorque", "R", "IfTorque", "5", action="MODIFY", rid=port_rid("SwcA", "RpTorque")),
            port_row(
                "SwcA", "RpTorque", "R", "IfTorque", "6", action="MODIFY", rid=port_rid("SwcA", "RpTorque"), file=BETA
            ),
        ],
        [R, R],
        (c.CFL_DIFFERENT,),
    ),
]

_PENDING_PORT = _pend_port("SwcM", "PpNew")
_OLD = "2026-10-01T08:00:00Z"

PENDING = [
    case(
        "pend: differs from a pending request",
        [port_row("SwcM", "PpNew", "P", "IfSpeed")],
        [R],
        (c.CFL_PENDING,),
        pending=(_PENDING_PORT,),
    ),
    case(
        "pend: repeats a pending request",
        [port_row("SwcM", "PpNew", "P", "IfTorque", "none")],
        [W],
        (c.PEND_ALREADY_QUEUED,),
        ok=(c.CFL_PENDING,),
        pending=(_PENDING_PORT,),
    ),
    case(
        "pend: fulfilled entries are closed",
        [],
        [],
        (c.PEND_FULFILLED,),
        ok=(c.PEND_MISMATCH, c.PEND_STALE, c.PEND_ORPHAN),
        pending=(
            _pend_port("SwcM", "PpTorque"),
            _pend(DeletePort(id="d1", swc_path=_M, name="RpGone", **_BASE)),
            _pend(
                AddTrigger(
                    id="t1", swc_path=_M, runnable_name="RunM", kind=TriggerKind.PERIODIC, period_s=0.01, **_BASE
                )
            ),
            _pend_connector("SwcA", "PpSpeed", "SwcB", "RpSpeed"),
        ),
    ),
    case(
        "pend: entries that still wait",
        [],
        [],
        ok=(c.PEND_FULFILLED, c.PEND_RELEASED, c.PEND_RELEASE_FAILED, c.PEND_STALE),
        pending=(
            _pend_port("SwcM", "PpNew", created="2026-11-10T08:00:00Z"),
            _pend_connector("SwcM", "PpNew", "SwcA", "RpTorque", created="2026-11-10T08:00:00Z"),
            _pend(
                AddAccessPoint(
                    id="a1",
                    swc_path=_M,
                    runnable_name="RunM",
                    port_name="RpSpeedM",
                    target="Speed",
                    kind=AccessKind.IMPLICIT_READ,
                    **_BASE,
                ),
                created="2026-11-10T08:00:00Z",
            ),
            _pend(
                AddServerRunnable(
                    id="s1", swc_path=_M, name="PpX_Op", symbol="PpX_Op", port_name="PpX", operation="Op", **_BASE
                ),
                created="2026-11-10T08:00:00Z",
            ),
        ),
    ),
    case(
        "pend: port exists with another direction",
        [],
        [],
        (c.PEND_MISMATCH,),
        pending=(_pend_port("SwcM", "PpTorque", "R"),),
    ),
    case(
        "pend: held connector is released",
        [],
        [],
        (c.PEND_RELEASED,),
        ok=(c.PEND_RELEASE_FAILED, c.PEND_ORPHAN),
        pending=(_pend_connector("SwcM", "PpTorque", "SwcA", "RpTorque"),),
    ),
    case(
        "pend: old entries are flagged",
        [],
        [],
        (c.PEND_STALE,),
        pending=(_pend_port("SwcM", "PpNew", created=_OLD), _pend_port("SwcM", "PpNew2", created="last week")),
    ),
    case(
        "pend: held connector that is no longer valid",
        [],
        [],
        (c.PEND_RELEASE_FAILED,),
        pending=(_pend_connector("SwcC", "PpSpeed8", "SwcC", "RpFree"), _pend_connector("SwcC", "PpDiagC", "SwcA", "RpDiag")),
    ),
    case(
        "pend: entries whose target is gone",
        [],
        [],
        (c.PEND_ORPHAN,),
        pending=(
            _pend_port("SwcGone", "PpNew"),
            _pend_connector("SwcGone", "PpX", "SwcA", "RpTorque"),
        ),
    ),
]

CONFIG = [
    case(
        "cfg: broken naming regex",
        [port_row("SwcA", "RpNew")],
        [R],
        (c.CFG_NAME_REGEX, c.NAME_PATTERN),
        naming=naming(port="(Rp"),
    ),
    case(
        "cfg: broken connector pattern",
        [
            connection_row("SwcD", "PpSpeedD", "SwcC", "RpFree"),
            connection_row("SwcA", "PpSpeed", "SwcM", "RpSpeedM", name="Asc_Named"),
        ],
        [R, A],
        (c.CFG_NAME_PATTERN,),
        naming=naming(connector_pattern="{provider}_{nope}"),
    ),
    case(
        "cfg: broken server runnable pattern",
        [port_row("SwcA", "PpDiagNew", "P", "IfDiag", "")],
        [R],
        (c.CFG_NAME_PATTERN,),
        naming=naming(server_runnable_pattern="{0}"),
    ),
    case(
        "cfg: custom patterns",
        [
            connection_row("SwcD", "PpSpeedD", "SwcC", "RpFree"),
            port_row("SwcA", "PpDiagNew", "P", "IfDiag", ""),
        ],
        [A, A],
        ok=(c.CFG_NAME_PATTERN, c.CFG_NAME_REGEX),
        naming=naming(
            connector_pattern="Asc_{provider_port}_{requester_port}",
            server_runnable_pattern="{swc}_{port}_{operation}",
            runnable="Swc\\w+",
        ),
    ),
]

ROUTING = [
    case(
        "route: connector to a port requested on a model-owned SWC",
        [port_row("SwcM", "PpNew", "P", "IfTorque"), connection_row("SwcM", "PpNew", "SwcA", "RpTorque")],
        [W, H],
    ),
    case(
        "route: connector to a port created by the same import",
        [connection_row("SwcA", "PpNew", "SwcC", "RpFree"), port_row("SwcA", "PpNew", "P")],
        [A, A],
        ok=(c.REF_UNKNOWN_PORT,),
    ),
    case(
        "route: connector to a port waiting in the pending queue",
        [connection_row("SwcM", "PpNew", "SwcA", "RpTorque")],
        [H],
        pending=(_PENDING_PORT,),
        ok=(c.REF_UNKNOWN_PORT, c.PEND_STALE),
        now="2026-11-02T08:00:00Z",
    ),
    case(
        "route: connector between existing ports of a model-owned SWC",
        [connection_row("SwcM", "PpTorque", "SwcA", "RpTorque")],
        [A],
    ),
    case(
        "route: connector to a port nobody requests",
        [connection_row("SwcM", "PpNobody", "SwcA", "RpTorque")],
        [R],
        (c.REF_UNKNOWN_PORT,),
    ),
    case(
        "route: connector to a rejected port request",
        [port_row("SwcM", "PpNew", "X", "IfTorque"), connection_row("SwcM", "PpNew", "SwcA", "RpTorque")],
        [R, R],
        (c.PORT_DIRECTION, c.REF_UNKNOWN_PORT),
    ),
    case(
        "route: new server port on a model-owned SWC",
        [port_row("SwcM", "PpDiagM", "P", "IfDiag", "")],
        [W],
    ),
    case(
        "route: operation-invoked trigger on a pending server port",
        [trigger_row("SwcM", "RunM", "on operation invocation", "PpDiagM", "ReadDtc")],
        [R],
        (c.TRG_SERVER_TAKEN,),
        pending=(_pend_port("SwcM", "PpDiagM", "P", "IfDiag"),),
    ),
]

CASES: list[Case] = [
    *ROW,
    *NAME,
    *REF,
    *OWN,
    *DATA_TYPES,
    *INTERFACES,
    *PORTS,
    *CONNECTORS,
    *ACCESS,
    *TRIGGERS,
    *CONFLICTS,
    *PENDING,
    *CONFIG,
    *ROUTING,
]
