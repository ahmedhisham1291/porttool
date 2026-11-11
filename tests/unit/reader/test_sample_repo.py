"""The reference repository: exact counts and attributes of everything the model holds."""

from __future__ import annotations

from types import MappingProxyType

import pytest

from porttool.core.config import OwnerEntry
from porttool.core.models import (
    AccessKind,
    ArgumentDirection,
    ArxmlOwner,
    ComponentKind,
    ConnectorKind,
    DataTypeCategory,
    DataTypeLevel,
    InterfaceKind,
    Model,
    PortDirection,
    TriggerKind,
)
from tests.unit.reader import samples
from tests.unit.reader.conftest import Repo
from tests.unit.reader.samples import BEH_A, BEH_B, CLUSTER, CT, DT, ECU, IF


@pytest.fixture
def model(any_repo: Repo) -> Model:
    samples.write_sample_repo(any_repo.root)
    owners = {
        "SwcA": OwnerEntry(swc="SwcA", team="Alpha"),
        "SwcM": OwnerEntry(swc="SwcM", team="Gamma", arxml_owner=ArxmlOwner.MODEL),
    }
    return any_repo.load(
        ecuc_file=any_repo.path(samples.F_ECUC),
        owners=MappingProxyType(owners),
        locked_swcs=frozenset({"CddSwc"}),
    )


def test_sample_repo_loads_without_findings(model: Model) -> None:
    assert [(f.code, f.message) for f in model.findings] == []


def test_component_counts_kinds_and_owners(model: Model) -> None:
    kinds = {c.name: c.kind for c in model.components.values()}
    assert kinds == {
        "SwcA": ComponentKind.APPLICATION,
        "SwcB": ComponentKind.APPLICATION,
        "SwcM": ComponentKind.APPLICATION,
        "SensorSwc": ComponentKind.SENSOR_ACTUATOR,
        "CddSwc": ComponentKind.COMPLEX_DEVICE_DRIVER,
        "IoHwAb": ComponentKind.ECU_ABSTRACTION,
        "NvM": ComponentKind.SERVICE,
        "DiagProxy": ComponentKind.SERVICE_PROXY,
        "NvBlocks": ComponentKind.NV_BLOCK,
        "CalSwc": ComponentKind.PARAMETER,
        "EcuComposition": ComponentKind.COMPOSITION,
        "Cluster": ComponentKind.COMPOSITION,
    }
    swc_a = model.components[f"{CT}/SwcA"]
    assert swc_a.owner is ArxmlOwner.DAVINCI and swc_a.team == "Alpha"
    assert swc_a.behavior_path == BEH_A
    assert swc_a.origin.file == samples.F_SWC_A
    assert swc_a.origin.uuid == "aaaaaaaa-0000-0000-0000-000000000001"
    assert swc_a.origin.line == 7
    swc_m = model.components[f"{CT}/SwcM"]
    assert swc_m.owner is ArxmlOwner.MODEL and swc_m.team == "Gamma"
    # a service component is locked without any configuration, locked.txt locks CddSwc
    assert model.components[f"{CT}/NvM"].owner is ArxmlOwner.LOCKED
    assert model.components[f"{CT}/CddSwc"].owner is ArxmlOwner.LOCKED
    assert model.components[f"{CT}/SwcB"].owner is ArxmlOwner.DAVINCI
    assert model.components[f"{CT}/SwcB"].team is None
    assert sorted(model.compositions) == [CLUSTER, ECU]


def test_swc_without_internal_behavior(model: Model) -> None:
    swc_m = model.components[f"{CT}/SwcM"]
    assert swc_m.behavior_path is None
    assert model.runnables_of(swc_m.path) == ()
    assert model.triggers_in(swc_m.path) == ()
    assert [p.name for p in model.ports_of(swc_m.path)] == ["PpTorque"]


def test_ports_directions_interfaces_and_com_specs(model: Model) -> None:
    assert len(model.ports) == 14
    ports_a = {p.name: p for p in model.ports_of(f"{CT}/SwcA")}
    assert {name: p.direction for name, p in ports_a.items()} == {
        "PpSpeed": PortDirection.P,
        "RpTorque": PortDirection.R,
        "RpDiag": PortDirection.R,
        "PpEvents": PortDirection.P,
        "RpMode": PortDirection.R,
        "PrTorque": PortDirection.PR,
    }
    speed = ports_a["PpSpeed"]
    assert speed.path == f"{CT}/SwcA/PpSpeed"
    assert speed.interface_path == f"{IF}/IfSpeed"
    assert [(s.target, s.init_value, s.queue_length) for s in speed.com_specs] == [
        ("Speed", "0", None)
    ]
    assert speed.origin.uuid == "aaaaaaaa-0000-0000-0000-000000000011"
    # an init value given as a constant reference is the path of the constant
    assert [(s.target, s.init_value) for s in ports_a["RpTorque"].com_specs] == [
        ("Torque", "/Constants/C_TorqueInit")
    ]
    assert ports_a["RpMode"].com_specs[0].target == "currentMode"
    ports_b = {p.name: p for p in model.ports_of(f"{CT}/SwcB")}
    assert [(s.target, s.queue_length) for s in ports_b["PpDiag"].com_specs] == [("ReadDtc", 1)]
    assert [(s.target, s.queue_length) for s in ports_b["RpEvents"].com_specs] == [("Event", 4)]
    nv = model.find_port(f"{CT}/NvBlocks", "PpNv")
    assert nv is not None and [(s.target, s.init_value) for s in nv.com_specs] == [("Stored", "5")]
    cal = model.find_port(f"{CT}/CalSwc", "PpCal")
    assert cal is not None and [(s.target, s.init_value) for s in cal.com_specs] == [
        ("Gain", "100")
    ]
    assert len(model.ports_using(f"{IF}/IfSpeed")) == 3  # SwcA, SwcB, Cluster outer port


def test_pr_port_has_provided_and_required_com_specs(model: Model) -> None:
    port = model.find_port(f"{CT}/SwcA", "PrTorque")
    assert port is not None
    assert port.direction is PortDirection.PR
    assert port.interface_path == f"{IF}/IfTorque"
    # provided side first, then required side
    assert [(s.target, s.init_value) for s in port.com_specs] == [("Torque", "1"), ("Torque", "2")]


def test_port_without_com_spec(model: Model) -> None:
    port = model.find_port(f"{CT}/SwcA", "RpDiag")
    assert port is not None
    assert port.com_specs == ()
    assert port.interface_path == f"{IF}/IfDiag"


def test_interfaces_of_every_kind(model: Model) -> None:
    assert {i.name: i.kind for i in model.interfaces.values()} == {
        "IfSpeed": InterfaceKind.SENDER_RECEIVER,
        "IfTorque": InterfaceKind.SENDER_RECEIVER,
        "IfEvents": InterfaceKind.SENDER_RECEIVER,
        "IfDiag": InterfaceKind.CLIENT_SERVER,
        "IfMode": InterfaceKind.MODE_SWITCH,
        "IfCal": InterfaceKind.PARAMETER,
        "IfNv": InterfaceKind.NV_DATA,
        "IfTrig": InterfaceKind.TRIGGER,
    }
    speed = model.interfaces[f"{IF}/IfSpeed"]
    assert [(e.name, e.type_path, e.is_queued) for e in speed.elements] == [
        ("Speed", f"{DT}/uint16", False)
    ]
    assert speed.is_service is False
    assert speed.origin.uuid == "22222222-0000-0000-0000-000000000001"
    events = model.interfaces[f"{IF}/IfEvents"].element("Event")
    assert events is not None and events.is_queued is True
    diag = model.interfaces[f"{IF}/IfDiag"]
    assert diag.is_service is True
    assert [o.name for o in diag.operations] == ["ReadDtc", "Clear"]
    read = diag.operation("ReadDtc")
    assert read is not None
    assert [(a.name, a.direction, a.type_path) for a in read.arguments] == [
        ("Index", ArgumentDirection.IN, f"{DT}/uint16"),
        ("Dtc", ArgumentDirection.OUT, f"{DT}/uint16"),
    ]
    assert read.possible_errors == ("E_NOT_OK",)
    mode = model.interfaces[f"{IF}/IfMode"]
    assert mode.mode_group_path == "/Modes/MdgEcu"
    assert [e.name for e in mode.elements] == ["currentMode"]
    gain = model.interfaces[f"{IF}/IfCal"].element("Gain")
    assert gain is not None and gain.init_value == "100"
    assert [e.name for e in model.interfaces[f"{IF}/IfNv"].elements] == ["Stored"]
    assert [e.name for e in model.interfaces[f"{IF}/IfTrig"].elements] == ["Tick"]


def test_runnables(model: Model) -> None:
    assert len(model.runnables) == 5
    run = model.find_runnable(f"{CT}/SwcA", "RunA_10ms")
    assert run is not None
    assert run.path == f"{BEH_A}/RunA_10ms"
    assert run.symbol == "RunA_10ms_Symbol"
    assert run.can_be_invoked_concurrently is False
    assert run.min_start_interval_s == 0.005
    assert run.origin.uuid == "aaaaaaaa-0000-0000-0000-0000000000f1"
    init = model.find_runnable(f"{CT}/SwcA", "RunA_Init")
    assert init is not None and init.can_be_invoked_concurrently is True
    assert init.min_start_interval_s is None
    assert [r.name for r in model.runnables_of(f"{CT}/SwcB")] == [
        "RunB_OnSpeed",
        "PpDiag_ReadDtc",
        "RunB_Background",
    ]


def test_access_points_of_every_kind(model: Model) -> None:
    assert len(model.access_points) == 9
    run_a = f"{BEH_A}/RunA_10ms"
    by_name = {a.path.rpartition("/")[2]: a for a in model.access_points_of_runnable(run_a)}
    assert {name: (a.kind, a.port_path, a.target) for name, a in by_name.items()} == {
        "DSP_PpEvents_Event": (AccessKind.EXPLICIT_SEND, f"{CT}/SwcA/PpEvents", "Event"),
        "DWA_PpSpeed_Speed": (AccessKind.IMPLICIT_WRITE, f"{CT}/SwcA/PpSpeed", "Speed"),
        "<MODE-ACCESS-POINT:RpMode.currentMode>": (
            AccessKind.MODE_ACCESS,
            f"{CT}/SwcA/RpMode",
            "currentMode",
        ),
        "SSCP_RpDiag_ReadDtc": (AccessKind.SYNC_SERVER_CALL, f"{CT}/SwcA/RpDiag", "ReadDtc"),
        "ASCP_RpDiag_Clear": (AccessKind.ASYNC_SERVER_CALL, f"{CT}/SwcA/RpDiag", "Clear"),
    }
    write = by_name["DWA_PpSpeed_Speed"]
    assert write.swc_path == f"{CT}/SwcA" and write.runnable_path == run_a
    assert write.origin.uuid == "aaaaaaaa-0000-0000-0000-0000000000a1"
    on_speed = {
        a.path.rpartition("/")[2]: a.kind
        for a in model.access_points_of_runnable(f"{BEH_B}/RunB_OnSpeed")
    }
    assert on_speed == {
        "DRPA_RpSpeed_Speed": AccessKind.EXPLICIT_READ,
        "DRPA_RpEvents_Event": AccessKind.QUEUED_RECEIVE,  # the element is queued
    }
    background = {
        a.path.rpartition("/")[2]: a.kind
        for a in model.access_points_of_runnable(f"{BEH_B}/RunB_Background")
    }
    assert background == {
        "DRA_RpSpeed_Speed": AccessKind.IMPLICIT_READ,
        "DRPV_RpSpeed_Speed": AccessKind.EXPLICIT_READ,
    }
    assert len(model.access_points_of_port(f"{CT}/SwcB/RpSpeed")) == 3


def test_triggers_of_every_kind(model: Model) -> None:
    assert len(model.triggers) == 8
    got = {
        t.name: (t.kind, t.runnable_path, t.port_path, t.target, t.period_s)
        for t in model.triggers.values()
    }
    assert got == {
        "TMT_RunA_10ms": (TriggerKind.PERIODIC, f"{BEH_A}/RunA_10ms", None, None, 0.01),
        "IT_RunA_Init": (TriggerKind.INIT, f"{BEH_A}/RunA_Init", None, None, None),
        "MST_RunA_OnRun": (
            TriggerKind.MODE_SWITCH,
            f"{BEH_A}/RunA_Init",
            f"{CT}/SwcA/RpMode",
            "RUN",
            None,
        ),
        # the port and element come from the data send point the event refers to
        "DSC_RunA_Sent": (
            TriggerKind.DATA_SEND_COMPLETED,
            f"{BEH_A}/RunA_Init",
            f"{CT}/SwcA/PpEvents",
            "Event",
            None,
        ),
        "DRT_RunB_OnSpeed": (
            TriggerKind.DATA_RECEIVED,
            f"{BEH_B}/RunB_OnSpeed",
            f"{CT}/SwcB/RpSpeed",
            "Speed",
            None,
        ),
        "DRE_RunB_OnSpeedError": (
            TriggerKind.DATA_RECEIVE_ERROR,
            f"{BEH_B}/RunB_OnSpeed",
            f"{CT}/SwcB/RpSpeed",
            "Speed",
            None,
        ),
        "OIT_PpDiag_ReadDtc": (
            TriggerKind.OPERATION_INVOKED,
            f"{BEH_B}/PpDiag_ReadDtc",
            f"{CT}/SwcB/PpDiag",
            "ReadDtc",
            None,
        ),
        "BGT_RunB_Background": (
            TriggerKind.BACKGROUND,
            f"{BEH_B}/RunB_Background",
            None,
            None,
            None,
        ),
    }
    timing = model.triggers[f"{BEH_A}/TMT_RunA_10ms"]
    assert timing.swc_path == f"{CT}/SwcA"
    assert timing.origin.uuid == "aaaaaaaa-0000-0000-0000-0000000000e1"
    assert len(model.triggers_of(f"{BEH_A}/RunA_Init")) == 3
    assert len(model.triggers_in(f"{CT}/SwcB")) == 4


def test_nested_compositions_and_delegation_connectors(model: Model) -> None:
    assert sorted(p.name for p in model.prototypes_in(ECU)) == ["Cluster", "SwcA", "SwcB", "SwcM"]
    cluster = model.prototypes[f"{ECU}/Cluster"]
    assert cluster.type_path == CLUSTER
    assert len(model.connectors) == 6
    assert len(model.connectors_in(ECU)) == 4 and len(model.connectors_in(CLUSTER)) == 2
    speed = model.connectors[f"{ECU}/SwcA_PpSpeed_SwcB_RpSpeed"]
    assert speed.kind is ConnectorKind.ASSEMBLY
    assert (speed.provider.prototype_path, speed.provider.port_path) == (
        f"{ECU}/SwcA",
        f"{CT}/SwcA/PpSpeed",
    )
    assert (speed.requester.prototype_path, speed.requester.port_path) == (
        f"{ECU}/SwcB",
        f"{CT}/SwcB/RpSpeed",
    )
    assert speed.origin.uuid == "cccccccc-0000-0000-0000-0000000000c1"
    # outer R port -> inner R port: the composition's own port is the provider side
    inward = model.connectors[f"{CLUSTER}/RpSpeedIn_SwcB1_RpSpeed"]
    assert inward.kind is ConnectorKind.DELEGATION
    assert (inward.provider.prototype_path, inward.provider.port_path) == (
        None,
        f"{CLUSTER}/RpSpeedIn",
    )
    assert (inward.requester.prototype_path, inward.requester.port_path) == (
        f"{CLUSTER}/SwcB1",
        f"{CT}/SwcB/RpSpeed",
    )
    # inner P port -> outer P port: the composition's own port is the requester side
    outward = model.connectors[f"{CLUSTER}/SwcB2_PpDiag_PpDiagOut"]
    assert (outward.provider.prototype_path, outward.provider.port_path) == (
        f"{CLUSTER}/SwcB2",
        f"{CT}/SwcB/PpDiag",
    )
    assert (outward.requester.prototype_path, outward.requester.port_path) == (
        None,
        f"{CLUSTER}/PpDiagOut",
    )
    assert len(model.connectors_at(None, f"{CLUSTER}/RpSpeedIn")) == 1
    # the nested composition's port, seen from the outer composition
    assert len(model.connectors_at(f"{ECU}/Cluster", f"{CLUSTER}/RpSpeedIn")) == 1
    assert len(model.connectors_at(f"{ECU}/SwcA", f"{CT}/SwcA/PpSpeed")) == 2


def test_one_component_type_instantiated_twice(model: Model) -> None:
    instances = sorted(p.path for p in model.prototypes_of_type(f"{CT}/SwcB"))
    assert instances == [f"{CLUSTER}/SwcB1", f"{CLUSTER}/SwcB2", f"{ECU}/SwcB"]
    # one port of the type, three port instances, each with its own connections
    keys = {i.key for i in model.port_instances() if i.port.path == f"{CT}/SwcB/RpSpeed"}
    assert keys == {(path, f"{CT}/SwcB/RpSpeed") for path in instances}
    assert len(model.connectors_at(f"{CLUSTER}/SwcB1", f"{CT}/SwcB/RpSpeed")) == 1
    assert model.connectors_at(f"{CLUSTER}/SwcB2", f"{CT}/SwcB/RpSpeed") == ()


def test_unconnected_port_instances(model: Model) -> None:
    unconnected = {(i.prototype.path.removeprefix(CT), i.port.name) for i in model.unconnected()}
    assert unconnected == {
        ("/EcuComposition/SwcA", "RpTorque"),
        ("/EcuComposition/SwcA", "RpMode"),
        ("/EcuComposition/SwcA", "PrTorque"),
        ("/EcuComposition/SwcM", "PpTorque"),
        ("/EcuComposition/Cluster", "PpDiagOut"),
        ("/Cluster/SwcB1", "PpDiag"),
        ("/Cluster/SwcB1", "RpEvents"),
        ("/Cluster/SwcB2", "RpSpeed"),
        ("/Cluster/SwcB2", "RpEvents"),
    }
    assert len(list(model.port_instances())) == 6 + 3 + 1 + 2 + 3 + 3


def test_data_types(model: Model) -> None:
    assert len(model.data_types) == 9
    types = model.data_types
    uint16 = types[f"{DT}/uint16"]
    assert (uint16.level, uint16.category) == (DataTypeLevel.IMPLEMENTATION, DataTypeCategory.VALUE)
    assert uint16.base_type_path == f"{DT}/BaseTypes/uint16"
    assert uint16.origin.uuid == "11111111-0000-0000-0000-000000000001"
    assert types[f"{DT}/boolean"].category is DataTypeCategory.BOOLEAN
    gear = types[f"{DT}/Gear_T"]
    assert gear.category is DataTypeCategory.ENUMERATION
    assert gear.compu_method_path == f"{DT}/CompuMethods/CM_Gear"
    alias = types[f"{DT}/Speed_Alias"]
    assert alias.category is DataTypeCategory.TYPE_REFERENCE
    assert alias.element_type_paths == (f"{DT}/uint16",) and alias.element_names == ()
    array = types[f"{DT}/Arr8"]
    assert (array.category, array.array_size) == (DataTypeCategory.ARRAY, 8)
    assert array.element_type_paths == (f"{DT}/uint16",)
    record = types[f"{DT}/Rec"]
    assert record.category is DataTypeCategory.RECORD
    assert record.element_names == ("speed", "valid")
    assert record.element_type_paths == (f"{DT}/uint16", f"{DT}/boolean")
    app = types[f"{DT}/AppSpeed"]
    assert (app.level, app.category) == (DataTypeLevel.APPLICATION, DataTypeCategory.VALUE)
    assert app.compu_method_path == f"{DT}/CompuMethods/CM_Speed"
    assert app.constraint_path == f"{DT}/DataConstrs/DC_Speed"
    assert app.unit_path == f"{DT}/Units/KmPerHour"
    assert app.base_type_path is None
    app_array = types[f"{DT}/AppSpeedArray"]
    assert (app_array.category, app_array.array_size) == (DataTypeCategory.ARRAY, 4)
    assert app_array.element_type_paths == (f"{DT}/AppSpeed",)
    app_record = types[f"{DT}/AppRec"]
    assert app_record.category is DataTypeCategory.RECORD
    assert app_record.element_names == ("front", "rear")
    assert app_record.element_type_paths == (f"{DT}/AppSpeed", f"{DT}/AppSpeed")


def test_base_types_compu_methods_constraints_units(model: Model) -> None:
    assert {b.name: (b.size_bits, b.encoding) for b in model.base_types.values()} == {
        "uint16": (16, "NONE"),
        "boolean": (8, "BOOLEAN"),
    }
    assert model.base_types[f"{DT}/BaseTypes/uint16"].origin.uuid is not None
    linear = model.compu_methods[f"{DT}/CompuMethods/CM_Speed"]
    assert (linear.category, linear.factor, linear.offset) == ("LINEAR", 0.01, -40.0)
    assert linear.unit_path == f"{DT}/Units/KmPerHour" and linear.enum_values == ()
    table = model.compu_methods[f"{DT}/CompuMethods/CM_Gear"]
    assert table.category == "TEXTTABLE"
    assert table.enum_values == ((0, "PARK"), (1, "REVERSE"), (2, "DRIVE"))
    assert table.factor is None and table.offset is None
    constraint = model.constraints[f"{DT}/DataConstrs/DC_Speed"]
    assert (constraint.lower, constraint.upper) == (-40.0, 615.35)
    unit = model.units[f"{DT}/Units/KmPerHour"]
    assert unit.display_name == "km/h"


def test_mapping_sets_and_constants(model: Model) -> None:
    assert len(model.mapping_sets) == 1
    assert model.mapping_sets[f"{DT}/Mappings"].pairs == ((f"{DT}/AppSpeed", f"{DT}/uint16"),)
    assert {c.name: c.value for c in model.constants.values()} == {
        "C_TorqueInit": "7",
        "C_Table": "[1, 2, {3, four}]",
    }


def test_tasks_and_task_mappings_from_the_ecuc_file(model: Model) -> None:
    assert model.tasks == frozenset({"Task_10ms", "Task_Event", "Task_Init"})
    got = {m.event_path: (m.task, m.position) for m in model.task_mappings.values()}
    assert got == {
        f"{BEH_A}/TMT_RunA_10ms": ("Task_10ms", 1),
        f"{BEH_A}/IT_RunA_Init": ("Task_Init", None),
        # mapped once per instance of SwcB to the same task: the first mapping is shown
        f"{BEH_B}/DRT_RunB_OnSpeed": ("Task_Event", 2),
    }
    # an event without task reference is not mapped
    assert model.task_of(f"{BEH_B}/OIT_PpDiag_ReadDtc") is None


def test_files_and_model_hash(model: Model, any_repo: Repo) -> None:
    assert sorted(model.files) == sorted([*samples.SAMPLE_FILES, samples.F_ECUC])
    source = model.files[samples.F_SWC_A]
    on_disk = any_repo.path(samples.F_SWC_A).stat()
    assert (source.size, source.mtime_ns) == (on_disk.st_size, on_disk.st_mtime_ns)
    assert source.sha256 is not None and len(source.sha256) == 64
    assert len(model.model_hash) == 64
