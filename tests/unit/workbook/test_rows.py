"""Model -> sheet rows: what each cell shows and in which order the rows come."""

from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import Path

import pytest

from porttool.core.config import OwnerEntry, ProjectConfig
from porttool.core.errors import WorkbookError
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
    Port,
    PortDirection,
    PortInterface,
    Trigger,
    TriggerKind,
    Unit,
)
from porttool.core.results import Finding, RequestResult, RequestStatus, RowRef, Severity
from porttool.core.rowid import interface_element_key, row_id, unconnected_key
from porttool.core.sheets import COLUMNS, Action, Sheet
from porttool.workbook import layout, lists
from porttool.workbook.rows import (
    SheetRows,
    build_all_rows,
    build_labels,
    component_rows,
    connection_rows,
    data_type_rows,
    init_value_text,
    interface_element_rows,
    number_cell,
    period_ms_cell,
    port_rows,
    result_rows,
    runnable_access_rows,
    runnable_trigger_rows,
    unconnected_rows,
)
from tests.helpers.sample_model import COMPOSITION, CT, DT, IF, build_sample_model

CellMap = Mapping[str, object]


def as_dicts(data: SheetRows) -> list[dict[str, object]]:
    keys = [column.key for column in COLUMNS[data.sheet]]
    return [dict(zip(keys, row, strict=True)) for row in data.rows]


def by(rows: list[dict[str, object]], key: str, value: object) -> dict[str, object]:
    matches = [row for row in rows if row[key] == value]
    assert len(matches) == 1, f"{len(matches)} rows with {key}={value!r}"
    return matches[0]


def swc(name: str) -> Component:
    return Component(path=f"{CT}/{name}", name=name, kind=ComponentKind.APPLICATION)


def port(swc_name: str, name: str, direction: PortDirection, interface: str | None) -> Port:
    return Port(
        path=f"{CT}/{swc_name}/{name}",
        name=name,
        swc_path=f"{CT}/{swc_name}",
        direction=direction,
        interface_path=f"{IF}/{interface}" if interface else None,
    )


def prototype(name: str, type_name: str, composition: str = COMPOSITION) -> ComponentPrototype:
    return ComponentPrototype(
        path=f"{composition}/{name}",
        name=name,
        composition_path=composition,
        type_path=f"{CT}/{type_name}",
    )


def sr_interface(name: str) -> PortInterface:
    return PortInterface(
        path=f"{IF}/{name}",
        name=name,
        kind=InterfaceKind.SENDER_RECEIVER,
        elements=(DataElement(name="Value", type_path=f"{DT}/uint16"),),
    )


def assembly(
    name: str, provider: tuple[str, str, str], requester: tuple[str, str, str]
) -> Connector:
    """``provider`` and ``requester`` are (prototype, SWC type, port)."""
    return Connector(
        path=f"{COMPOSITION}/{name}",
        name=name,
        composition_path=COMPOSITION,
        kind=ConnectorKind.ASSEMBLY,
        provider=ConnectorEnd(
            prototype_path=f"{COMPOSITION}/{provider[0]}",
            port_path=f"{CT}/{provider[1]}/{provider[2]}",
        ),
        requester=ConnectorEnd(
            prototype_path=f"{COMPOSITION}/{requester[0]}",
            port_path=f"{CT}/{requester[1]}/{requester[2]}",
        ),
    )


# --------------------------------------------------------------------------- sample model


def test_every_row_is_aligned_with_the_layout_columns(model: Model, config: ProjectConfig) -> None:
    sheets = build_all_rows(model, config, (), build_labels(model), partner_limit=10)
    assert set(sheets) == set(COLUMNS)
    for sheet, data in sheets.items():
        assert data.sheet is sheet
        assert len(data.rows) == len(data.paths)
        for row in data.rows:
            assert len(row) == len(COLUMNS[sheet])


def test_request_cells_are_empty_on_export(model: Model, config: ProjectConfig) -> None:
    sheets = build_all_rows(model, config, (), build_labels(model), partner_limit=10)
    for sheet in (s for s in sheets if layout.is_request_sheet(s)):
        for row in as_dicts(sheets[sheet]):
            assert (row["action"], row["requester"], row["comment"]) == ("", "", "")


def test_ports_sheet_of_the_sample_model(model: Model) -> None:
    rows = as_dicts(port_rows(model, build_labels(model)))
    assert [(r["swc"], r["port"]) for r in rows] == [
        ("SwcA", "PpSpeed"),
        ("SwcA", "RpDiag"),
        ("SwcA", "RpTorque"),
        ("SwcB", "PpDiag"),
        ("SwcB", "RpSpeed"),
        ("SwcM", "PpTorque"),
    ]
    speed = rows[0]
    assert speed == {
        "row_id": row_id(Sheet.PORTS, f"{CT}/SwcA/PpSpeed"),
        "swc": "SwcA",
        "port": "PpSpeed",
        "direction": "P",
        "interface": "IfSpeed",
        "interface_kind": "sender-receiver",
        "connected": "Y",
        "partner_count": 1,
        "init_value": "Speed=0",
        "action": "",
        "requester": "",
        "comment": "",
    }
    torque = by(rows, "port", "RpTorque")
    assert (torque["connected"], torque["partner_count"]) == ("N", 0)
    assert by(rows, "port", "RpDiag")["init_value"] == ""


def test_port_is_connected_when_any_prototype_of_the_type_has_a_connector() -> None:
    model = Model(
        components=[swc("Sender"), swc("Receiver")],
        interfaces=[sr_interface("IfX")],
        ports=[
            port("Sender", "PpX", PortDirection.P, "IfX"),
            port("Receiver", "RpX", PortDirection.R, "IfX"),
        ],
        compositions=[Composition(path=COMPOSITION, name="EcuComposition")],
        prototypes=[
            prototype("Sender", "Sender"),
            prototype("ReceiverLeft", "Receiver"),
            prototype("ReceiverRight", "Receiver"),
            prototype("ReceiverSpare", "Receiver"),
        ],
        connectors=[
            assembly("c1", ("Sender", "Sender", "PpX"), ("ReceiverLeft", "Receiver", "RpX")),
            assembly("c2", ("Sender", "Sender", "PpX"), ("ReceiverRight", "Receiver", "RpX")),
        ],
    )
    rows = as_dicts(port_rows(model, build_labels(model)))
    assert (by(rows, "port", "PpX")["connected"], by(rows, "port", "PpX")["partner_count"]) == (
        "Y",
        2,
    )
    # Two of the three Receiver prototypes are connected: the type's port counts both.
    assert (by(rows, "port", "RpX")["connected"], by(rows, "port", "RpX")["partner_count"]) == (
        "Y",
        2,
    )
    unconnected = as_dicts(unconnected_rows(model, build_labels(model), partner_limit=10))
    assert [(r["swc"], r["port"]) for r in unconnected] == [("ReceiverSpare", "RpX")]


def test_port_without_prototype_or_interface_is_not_connected() -> None:
    model = Model(components=[swc("Lonely")], ports=[port("Lonely", "PpX", PortDirection.P, None)])
    row = as_dicts(port_rows(model, build_labels(model)))[0]
    assert (row["connected"], row["partner_count"], row["interface"], row["interface_kind"]) == (
        "N",
        0,
        "",
        "",
    )


def test_interface_elements_one_row_per_element_and_per_operation_argument(model: Model) -> None:
    rows = as_dicts(interface_element_rows(model, build_labels(model)))
    assert [(r["interface"], r["element"], r["argument"]) for r in rows] == [
        ("IfDiag", "ReadDtc", "Dtc"),
        ("IfSpeed", "Speed", ""),
        ("IfTorque", "Torque", ""),
    ]
    dtc = rows[0]
    assert dtc["row_id"] == row_id(
        Sheet.INTERFACE_ELEMENTS, interface_element_key(f"{IF}/IfDiag", "ReadDtc", "Dtc")
    )
    assert (dtc["direction"], dtc["data_type"], dtc["queued"]) == ("OUT", "uint16", "")
    speed = rows[1]
    assert speed["row_id"] == row_id(
        Sheet.INTERFACE_ELEMENTS, interface_element_key(f"{IF}/IfSpeed", "Speed")
    )
    assert (speed["direction"], speed["data_type"], speed["queued"]) == ("", "uint16", "N")


def test_operation_without_arguments_gets_one_row_with_empty_argument() -> None:
    interface = PortInterface(
        path=f"{IF}/IfService",
        name="IfService",
        kind=InterfaceKind.CLIENT_SERVER,
        operations=(
            Operation(name="Reset"),
            Operation(
                name="Read",
                arguments=(
                    Argument(name="Id", direction=ArgumentDirection.IN, type_path=f"{DT}/u8"),
                    Argument(name="Data", direction=ArgumentDirection.OUT, type_path=None),
                ),
            ),
        ),
    )
    queued = PortInterface(
        path=f"{IF}/IfEvent",
        name="IfEvent",
        kind=InterfaceKind.SENDER_RECEIVER,
        elements=(DataElement(name="Event", type_path=None, is_queued=True),),
    )
    model = Model(interfaces=[interface, queued])
    rows = as_dicts(interface_element_rows(model, build_labels(model)))
    assert [(r["element"], r["argument"], r["direction"], r["queued"]) for r in rows] == [
        ("Event", "", "", "Y"),
        ("Reset", "", "", ""),
        ("Read", "Id", "IN", ""),
        ("Read", "Data", "OUT", ""),
    ]
    assert rows[1]["row_id"] == row_id(
        Sheet.INTERFACE_ELEMENTS, interface_element_key(f"{IF}/IfService", "Reset", "")
    )
    # A dangling type reference shows its path, a missing one stays empty.
    assert (rows[2]["data_type"], rows[3]["data_type"]) == (f"{DT}/u8", "")


def test_runnable_access_and_trigger_rows_of_the_sample_model(model: Model) -> None:
    labels = build_labels(model)
    access = as_dicts(runnable_access_rows(model, labels))
    assert [
        (r["swc"], r["runnable"], r["port"], r["target"], r["access_kind"]) for r in access
    ] == [
        ("SwcA", "RunA_10ms", "PpSpeed", "Speed", "implicit write"),
        ("SwcA", "RunA_10ms", "RpDiag", "ReadDtc", "synchronous server call"),
        ("SwcB", "RunB_OnSpeed", "RpSpeed", "Speed", "explicit read"),
    ]
    behavior = f"{CT}/SwcA/SwcA_InternalBehavior"
    assert access[0]["row_id"] == row_id(
        Sheet.RUNNABLE_ACCESS, f"{behavior}/RunA_10ms/DWA_PpSpeed_Speed"
    )
    triggers = as_dicts(runnable_trigger_rows(model, labels))
    periodic = by(triggers, "runnable", "RunA_10ms")
    assert periodic["row_id"] == row_id(Sheet.RUNNABLE_TRIGGERS, f"{behavior}/TMT_RunA_10ms")
    assert (periodic["trigger_kind"], periodic["period_ms"], periodic["task"]) == (
        "periodic",
        10,
        "Task_10ms",
    )
    server = by(triggers, "runnable", "PpDiag_ReadDtc")
    assert (server["port"], server["target"], server["period_ms"], server["task"]) == (
        "PpDiag",
        "ReadDtc",
        "",
        "",
    )


def test_connections_show_prototype_names_and_delegation_ends_stay_empty(model: Model) -> None:
    rows = as_dicts(connection_rows(model, build_labels(model)))
    first = rows[0]
    assert first == {
        "row_id": row_id(Sheet.CONNECTIONS, f"{COMPOSITION}/SwcA_PpSpeed_SwcB_RpSpeed"),
        "provider_swc": "SwcA",
        "provider_port": "PpSpeed",
        "requester_swc": "SwcB",
        "requester_port": "RpSpeed",
        "composition": "EcuComposition",
        "connector_name": "SwcA_PpSpeed_SwcB_RpSpeed",
        "action": "",
        "requester": "",
        "comment": "",
    }
    delegation = Connector(
        path=f"{COMPOSITION}/Deleg",
        name="Deleg",
        composition_path=COMPOSITION,
        kind=ConnectorKind.DELEGATION,
        provider=ConnectorEnd(prototype_path=f"{COMPOSITION}/Inner", port_path=f"{CT}/X/PpOut"),
        requester=ConnectorEnd(prototype_path=None, port_path=f"{COMPOSITION}/PpOuter"),
    )
    lonely = Model(connectors=[delegation])
    row = as_dicts(connection_rows(lonely, build_labels(lonely)))[0]
    # Nothing of this connector is in the model: names fall back to the last path segment.
    assert (row["provider_swc"], row["provider_port"]) == ("Inner", "PpOut")
    assert (row["requester_swc"], row["requester_port"]) == ("", "PpOuter")
    assert row["composition"] == COMPOSITION


def test_unconnected_sheet_of_the_sample_model(model: Model) -> None:
    rows = as_dicts(unconnected_rows(model, build_labels(model), partner_limit=10))
    assert rows == [
        {
            "row_id": row_id(
                Sheet.UNCONNECTED, unconnected_key(f"{COMPOSITION}/SwcA", f"{CT}/SwcA/RpTorque")
            ),
            "swc": "SwcA",
            "port": "RpTorque",
            "direction": "R",
            "interface": "IfTorque",
            "compatible_partners": "SwcM.PpTorque",
        },
        {
            "row_id": row_id(
                Sheet.UNCONNECTED, unconnected_key(f"{COMPOSITION}/SwcM", f"{CT}/SwcM/PpTorque")
            ),
            "swc": "SwcM",
            "port": "PpTorque",
            "direction": "P",
            "interface": "IfTorque",
            "compatible_partners": "SwcA.RpTorque",
        },
    ]


def test_compatible_partners_are_limited_and_the_rest_is_counted() -> None:
    receivers = [f"Rx{index:02d}" for index in range(13)]
    other = "/Other/Composition"
    model = Model(
        components=[swc("Tx"), *(swc(name) for name in receivers), swc("Far")],
        interfaces=[sr_interface("IfX"), sr_interface("IfY")],
        ports=[
            port("Tx", "PpX", PortDirection.P, "IfX"),
            port("Tx", "PpOther", PortDirection.P, "IfY"),
            port("Tx", "PpNoInterface", PortDirection.P, None),
            *(port(name, "RpX", PortDirection.R, "IfX") for name in receivers),
            port("Far", "RpX", PortDirection.R, "IfX"),
        ],
        prototypes=[
            prototype("Tx", "Tx"),
            *(prototype(name, name) for name in receivers),
            prototype("Far", "Far", composition=other),  # same interface, other composition
        ],
    )
    rows = as_dicts(unconnected_rows(model, build_labels(model), partner_limit=10))
    sender = next(r for r in rows if (r["swc"], r["port"]) == ("Tx", "PpX"))
    expected = "; ".join(f"Rx{index:02d}.RpX" for index in range(10)) + " (+3 more)"
    assert sender["compatible_partners"] == expected
    # The opposite direction sees the one sender; a port of another composition sees nobody.
    assert next(r for r in rows if r["swc"] == "Rx05")["compatible_partners"] == "Tx.PpX"
    assert next(r for r in rows if r["swc"] == "Far")["compatible_partners"] == ""
    assert next(r for r in rows if r["port"] == "PpOther")["compatible_partners"] == ""
    assert next(r for r in rows if r["port"] == "PpNoInterface")["compatible_partners"] == ""
    limited = as_dicts(unconnected_rows(model, build_labels(model), partner_limit=0))
    assert next(r for r in limited if r["port"] == "PpX")["compatible_partners"] == "(+13 more)"


def test_pr_port_is_compatible_with_every_direction_but_not_with_itself() -> None:
    model = Model(
        components=[swc("A"), swc("B"), swc("C"), swc("D")],
        interfaces=[sr_interface("IfX")],
        ports=[
            port("A", "PrpX", PortDirection.PR, "IfX"),
            port("B", "PpX", PortDirection.P, "IfX"),
            port("C", "RpX", PortDirection.R, "IfX"),
            port("D", "PrpX", PortDirection.PR, "IfX"),
        ],
        prototypes=[prototype(name, name) for name in "ABCD"],
    )
    rows = as_dicts(unconnected_rows(model, build_labels(model), partner_limit=10))
    assert by(rows, "swc", "A")["compatible_partners"] == "B.PpX; C.RpX; D.PrpX"
    assert by(rows, "swc", "B")["compatible_partners"] == "A.PrpX; C.RpX; D.PrpX"
    assert by(rows, "swc", "C")["compatible_partners"] == "A.PrpX; B.PpX; D.PrpX"
    limited = as_dicts(unconnected_rows(model, build_labels(model), partner_limit=1))
    assert by(limited, "swc", "A")["compatible_partners"] == "B.PpX (+2 more)"


# --------------------------------------------------------------------------- data types


def data_type_model() -> Model:
    base = BaseType(path=f"{DT}/BaseTypes/uint8", name="uint8", size_bits=8)
    unit = Unit(path=f"{DT}/Units/km_h", name="km_h")
    return Model(
        base_types=[base],
        units=[unit],
        compu_methods=[
            CompuMethod(
                path=f"{DT}/CM/Speed",
                name="CM_Speed",
                category="LINEAR",
                factor=0.01,
                offset=-40.0,
                unit_path=unit.path,
            ),
            CompuMethod(
                path=f"{DT}/CM/OnOff",
                name="CM_OnOff",
                category="TEXTTABLE",
                enum_values=((0, "OFF"), (1, "ON")),
            ),
        ],
        constraints=[
            DataConstraint(path=f"{DT}/DC/Speed", name="DC_Speed", lower=0.0, upper=655.35),
            DataConstraint(path=f"{DT}/DC/Open", name="DC_Open", lower=-math.inf, upper=None),
        ],
        data_types=[
            DataType(
                path=f"{DT}/Speed",
                name="Speed",
                level=DataTypeLevel.APPLICATION,
                category=DataTypeCategory.VALUE,
                base_type_path=base.path,
                compu_method_path=f"{DT}/CM/Speed",
                constraint_path=f"{DT}/DC/Speed",
            ),
            DataType(
                path=f"{DT}/OnOff",
                name="OnOff",
                level=DataTypeLevel.APPLICATION,
                category=DataTypeCategory.ENUMERATION,
                compu_method_path=f"{DT}/CM/OnOff",
                constraint_path=f"{DT}/DC/Open",
                unit_path=unit.path,
            ),
            DataType(
                path=f"{DT}/SpeedArray",
                name="SpeedArray",
                level=DataTypeLevel.IMPLEMENTATION,
                category=DataTypeCategory.ARRAY,
                element_type_paths=(f"{DT}/Speed",),
                array_size=4,
            ),
            DataType(
                path=f"{DT}/Status",
                name="Status",
                level=DataTypeLevel.IMPLEMENTATION,
                category=DataTypeCategory.RECORD,
                element_type_paths=(f"{DT}/Speed", f"{DT}/OnOff"),
                element_names=("speed", "state"),
                base_type_path=f"{DT}/BaseTypes/missing",
            ),
        ],
    )


def test_data_type_cells() -> None:
    model = data_type_model()
    rows = as_dicts(data_type_rows(model, build_labels(model)))
    assert [r["name"] for r in rows] == ["OnOff", "Speed", "SpeedArray", "Status"]
    speed = by(rows, "name", "Speed")
    assert speed == {
        "row_id": row_id(Sheet.DATA_TYPES, f"{DT}/Speed"),
        "name": "Speed",
        "level": "application",
        "category": "value",
        "base_type": "uint8",
        "lower_limit": 0,
        "upper_limit": 655.35,
        "unit": "km_h",  # taken from the compu method
        "factor": 0.01,
        "offset": -40,
        "enum_values": "",
        "element_type": "",
        "array_size": "",
        "record_elements": "",
        "action": "",
        "requester": "",
        "comment": "",
    }
    on_off = by(rows, "name", "OnOff")
    assert (on_off["enum_values"], on_off["unit"]) == ("0=OFF;1=ON", "km_h")
    assert (on_off["lower_limit"], on_off["upper_limit"]) == ("-inf", "")
    array = by(rows, "name", "SpeedArray")
    assert (array["element_type"], array["array_size"], array["record_elements"]) == (
        "Speed",
        4,
        "",
    )
    record = by(rows, "name", "Status")
    assert (record["record_elements"], record["element_type"]) == ("speed:Speed;state:OnOff", "")
    assert record["base_type"] == f"{DT}/BaseTypes/missing"  # dangling reference: the path


def test_number_cells() -> None:
    assert number_cell(None) == ""
    assert number_cell(4) == 4 and isinstance(number_cell(4), int)
    assert number_cell(65535.0) == 65535 and isinstance(number_cell(65535.0), int)
    assert number_cell(0.5) == 0.5
    assert number_cell(math.nan) == "nan"
    assert number_cell(math.inf) == "inf"
    assert number_cell(1e300) == 1e300 and isinstance(number_cell(1e300), float)


def test_period_is_shown_in_milliseconds_without_binary_noise() -> None:
    assert 0.01597 * 1000.0 != 15.97  # the reason the rounding exists
    assert period_ms_cell(0.01597) == 15.97
    assert period_ms_cell(0.007) == 7 and isinstance(period_ms_cell(0.007), int)
    assert period_ms_cell(0.01) == 10
    assert period_ms_cell(0.0025) == 2.5
    assert period_ms_cell(1.0) == 1000
    assert period_ms_cell(None) == ""


def test_init_value_text_lists_each_element_that_has_a_value() -> None:
    assert init_value_text([("Speed", "0"), ("Valid", None), ("Gear", "N")]) == "Speed=0;Gear=N"
    assert init_value_text([]) == ""
    model = Model(
        components=[swc("A")],
        ports=[
            Port(
                path=f"{CT}/A/Pp",
                name="Pp",
                swc_path=f"{CT}/A",
                direction=PortDirection.P,
                interface_path=None,
                com_specs=(ComSpec("Speed", "0"), ComSpec("Valid", None), ComSpec("Gear", "N")),
            )
        ],
    )
    assert as_dicts(port_rows(model, build_labels(model)))[0]["init_value"] == "Speed=0;Gear=N"


# --------------------------------------------------------------------------- names and order


def test_a_short_name_that_repeats_is_shown_as_path() -> None:
    model = Model(
        components=[
            swc("Unique"),
            Component(path="/PkgA/Twin", name="Twin", kind=ComponentKind.APPLICATION),
            Component(path="/PkgB/Twin", name="Twin", kind=ComponentKind.APPLICATION),
        ],
        interfaces=[
            PortInterface(path="/IfA/IfTwin", name="IfTwin", kind=InterfaceKind.SENDER_RECEIVER),
            PortInterface(path="/IfB/IfTwin", name="IfTwin", kind=InterfaceKind.SENDER_RECEIVER),
            sr_interface("IfUnique"),
        ],
        ports=[
            Port(
                path="/PkgA/Twin/Pp",
                name="Pp",
                swc_path="/PkgA/Twin",
                direction=PortDirection.P,
                interface_path="/IfB/IfTwin",
            ),
            port("Unique", "Pp", PortDirection.P, "IfUnique"),
        ],
    )
    labels = build_labels(model)
    rows = as_dicts(port_rows(model, labels))
    assert [(r["swc"], r["interface"]) for r in rows] == [
        ("/PkgA/Twin", "/IfB/IfTwin"),
        ("Unique", "IfUnique"),
    ]
    dropdowns = lists.build_lists(model, labels)
    assert dropdowns["swcs"] == ("/PkgA/Twin", "/PkgB/Twin", "Unique")
    assert dropdowns["interfaces"] == ("/IfA/IfTwin", "/IfB/IfTwin", "IfUnique")


def test_rows_do_not_depend_on_the_order_the_model_was_built_in(
    model: Model, config: ProjectConfig
) -> None:
    reversed_model = Model(
        components=reversed(list(model.components.values())),
        ports=reversed(list(model.ports.values())),
        interfaces=reversed(list(model.interfaces.values())),
        runnables=reversed(list(model.runnables.values())),
        access_points=reversed(list(model.access_points.values())),
        triggers=reversed(list(model.triggers.values())),
        task_mappings=reversed(list(model.task_mappings.values())),
        tasks=reversed(sorted(model.tasks)),
        compositions=model.compositions.values(),
        prototypes=reversed(list(model.prototypes.values())),
        connectors=reversed(list(model.connectors.values())),
        data_types=model.data_types.values(),
        base_types=model.base_types.values(),
        files=model.files.values(),
        baseline_commit=model.baseline_commit,
    )
    first = build_all_rows(model, config, (), build_labels(model), 10)
    second = build_all_rows(reversed_model, config, (), build_labels(reversed_model), 10)
    assert first == second
    assert lists.build_lists(model, build_labels(model)) == lists.build_lists(
        reversed_model, build_labels(reversed_model)
    )


def test_dangling_references_fall_back_to_the_last_path_segment() -> None:
    behavior = f"{CT}/Ghost/Ghost_InternalBehavior"
    model = Model(
        access_points=[
            AccessPoint(
                path=f"{behavior}/Run/Access",
                swc_path=f"{CT}/Ghost",
                runnable_path=f"{behavior}/Run",
                port_path=None,
                target=None,
                kind=AccessKind.OTHER,
            )
        ],
        triggers=[
            Trigger(
                path=f"{behavior}/Init",
                name="Init",
                swc_path=f"{CT}/Ghost",
                runnable_path=None,
                kind=TriggerKind.INIT,
            )
        ],
    )
    labels = build_labels(model)
    access = as_dicts(runnable_access_rows(model, labels))[0]
    assert (access["swc"], access["runnable"], access["port"], access["target"]) == (
        f"{CT}/Ghost",
        "Run",
        "",
        "",
    )
    trigger = as_dicts(runnable_trigger_rows(model, labels))[0]
    assert (trigger["runnable"], trigger["port"], trigger["task"]) == ("", "", "")


# --------------------------------------------------------------------------- components


def test_components_sheet_of_the_sample_model(model: Model, config: ProjectConfig) -> None:
    rows = as_dicts(component_rows(model, config))
    assert [r["name"] for r in rows] == ["EcuComposition", "SwcA", "SwcB", "SwcM"]
    assert by(rows, "name", "SwcM") == {
        "name": "SwcM",
        "kind": "application",
        "package_path": CT,
        "source_file": "Config/Developer/ComponentTypes/SwcM.arxml",
        "team": "Gamma",
        "arxml_owner": "model",
    }
    assert by(rows, "name", "EcuComposition")["team"] == ""


def test_component_owner_and_team_fall_back_to_the_configuration(tmp_path: Path) -> None:
    model = Model(components=[swc("Plain"), swc("Locked"), swc("Modelled")])
    config = ProjectConfig(
        repo_root=tmp_path,
        owners={
            "Plain": OwnerEntry(swc="Plain", team="Alpha"),
            "Modelled": OwnerEntry(swc="Modelled", arxml_owner=ArxmlOwner.MODEL),
        },
        locked_swcs=frozenset({"Locked"}),
    )
    rows = as_dicts(component_rows(model, config))
    assert (by(rows, "name", "Plain")["team"], by(rows, "name", "Plain")["arxml_owner"]) == (
        "Alpha",
        "davinci",
    )
    assert by(rows, "name", "Locked")["arxml_owner"] == "locked"
    assert by(rows, "name", "Modelled")["arxml_owner"] == "model"


# --------------------------------------------------------------------------- results


def test_result_rows_one_per_finding_and_one_for_a_result_without_findings() -> None:
    where = RowRef(file=r"C:\requests\team_a.xlsx", sheet="Ports", row=14)
    results = [
        RequestResult(
            request_id="REQ-1",
            status=RequestStatus.REJECTED,
            row=where,
            findings=(
                Finding(code="VAL-NAME-001", severity=Severity.ERROR, message="Bad port name."),
                Finding(
                    code="VAL-REF-002",
                    severity=Severity.ERROR,
                    message="Interface not found.",
                    object_path="/PortInterfaces/IfNope",
                ),
            ),
        ),
        RequestResult(request_id="REQ-2", status=RequestStatus.APPLIED, row=where),
        RequestResult(
            request_id="REQ-3",
            status=RequestStatus.ON_HOLD,
            findings=(Finding(code="VAL-HOLD-001", severity=Severity.INFO, message="Waiting."),),
        ),
    ]
    data = result_rows(results)
    assert as_dicts(data) == [
        {
            "row_id": "REQ-1",
            "sheet": "Ports",
            "status": "rejected",
            "code": "VAL-NAME-001",
            "reason": "Bad port name. [team_a.xlsx, row 14]",
        },
        {
            "row_id": "REQ-1",
            "sheet": "Ports",
            "status": "rejected",
            "code": "VAL-REF-002",
            "reason": "Interface not found. [team_a.xlsx, row 14]",
        },
        {
            "row_id": "REQ-2",
            "sheet": "Ports",
            "status": "applied",
            "code": "",
            "reason": "[team_a.xlsx, row 14]",
        },
        {
            "row_id": "REQ-3",
            "sheet": "",
            "status": "on hold",
            "code": "VAL-HOLD-001",
            "reason": "Waiting.",
        },
    ]
    assert data.paths == ("", "/PortInterfaces/IfNope", "", "")
    assert result_rows(()).rows == ()


# --------------------------------------------------------------------------- lists and layout


def test_every_dropdown_of_the_layout_has_a_list(model: Model) -> None:
    dropdowns = lists.build_lists(model, build_labels(model))
    assert layout.dropdown_names() <= dropdowns.keys()
    assert list(dropdowns) == sorted(dropdowns)
    assert dropdowns["actions"] == tuple(member.value for member in Action)
    assert dropdowns["yes_no"] == ("Y", "N")
    assert dropdowns["swcs"] == ("EcuComposition", "SwcA", "SwcB", "SwcM")
    assert dropdowns["prototypes"] == ("SwcA", "SwcB", "SwcM")
    assert dropdowns["interfaces"] == ("IfDiag", "IfSpeed", "IfTorque")
    assert dropdowns["data_types"] == ("uint16",)
    assert dropdowns["base_types"] == ("uint16",)
    assert dropdowns["units"] == ()
    assert dropdowns["tasks"] == ("Task_10ms", "Task_Event", "Task_Init")
    assert dropdowns["compositions"] == ("EcuComposition",)
    assert dropdowns["port_directions"] == ("P", "R", "PR")
    assert lists.ENUM_LISTS <= dropdowns.keys()


def test_a_dropdown_the_exporter_cannot_fill_is_a_workbook_error(
    model: Model, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lists, "dropdown_names", lambda: frozenset({"actions", "colours"}))
    with pytest.raises(WorkbookError) as raised:
        lists.build_lists(model, build_labels(model))
    assert raised.value.detail == "colours"
    assert raised.value.code == "PT-E300"


def test_sheet_order_covers_every_sheet_exactly_once() -> None:
    assert sorted(layout.SHEET_ORDER) == sorted(Sheet)
    assert [sheet.value for sheet in layout.SHEET_ORDER] == [
        "README",
        "Components",
        "DataTypes",
        "Interfaces",
        "InterfaceElements",
        "Ports",
        "RunnableAccess",
        "RunnableTriggers",
        "Connections",
        "Unconnected",
        "Results",
        "Lists",
        "Meta",
    ]
    assert layout.HIDDEN_SHEETS == {Sheet.LISTS, Sheet.META}


def test_row_id_patterns_follow_the_row_id_contract() -> None:
    for sheet in (*layout.SHEET_ORDER,):
        if sheet not in COLUMNS or "row_id" not in {c.key for c in COLUMNS[sheet]}:
            continue
        if sheet is Sheet.RESULTS:
            continue
        pattern = layout.row_id_pattern(sheet)
        assert pattern.fullmatch(row_id(sheet, "/some/path")) is not None
        assert pattern.fullmatch(row_id(sheet, "/some/path").upper()) is None
        assert pattern.fullmatch("XX-0123456789ab") is None
    assert layout.row_id_prefix(Sheet.PORTS) == "PT"
    assert layout.list_range_name("swcs") == "pt_swcs"


def test_sample_model_fixture_is_the_documented_one() -> None:
    assert len(build_sample_model().ports) == 6
