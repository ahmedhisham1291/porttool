"""AddInterface and AddDataType: element content, placement, new files."""

from __future__ import annotations

from typing import Any

from porttool.core.models import (
    Argument,
    ArgumentDirection,
    DataElement,
    DataTypeCategory,
    DataTypeLevel,
    InterfaceKind,
    Operation,
)
from porttool.core.operations import (
    AddDataType,
    AddInterface,
    AnyOperation,
    ArgumentSpec,
    ElementSpec,
    OperationSpec,
    RecordElementSpec,
)
from porttool.writer import codes
from tests.unit.writer.conftest import (
    DT,
    IF,
    Repo,
    added,
    assert_only_insertions,
    codes_of,
    errors,
)

IF_FILE = "Config/Developer/PortInterfaces/PortInterfaces.arxml"
DT_FILE = "Config/Developer/DataTypes/DataTypes.arxml"
UINT8 = f"{DT}/BaseTypes/uint8"
BOOLEAN = f"{DT}/BaseTypes/boolean"
BASE_FILE = "Config/Developer/DataTypes/BaseTypes.arxml"
CM = f"{DT}/CompuMethods"
DC = f"{DT}/DataConstrs"


def data_type(name: str, category: str, level: str = "implementation", **more: Any) -> AddDataType:
    return AddDataType(
        id=f"dt-{name}",
        package_path=more.pop("package_path", DT),
        name=name,
        level=DataTypeLevel(level),
        category=DataTypeCategory(category),
        **more,
    )


def sr_interface(name: str, *elements: tuple[str, str, bool], package: str = IF) -> AddInterface:
    return AddInterface(
        id=f"if-{name}",
        package_path=package,
        name=name,
        kind=InterfaceKind.SENDER_RECEIVER,
        elements=tuple(ElementSpec(name=n, type_path=t, is_queued=q) for n, t, q in elements),
    )


def cs_interface(name: str, package: str = IF) -> AddInterface:
    return AddInterface(
        id=f"if-{name}",
        package_path=package,
        name=name,
        kind=InterfaceKind.CLIENT_SERVER,
        operations=(
            OperationSpec(
                name="Get",
                arguments=(
                    ArgumentSpec(
                        name="Id", direction=ArgumentDirection.IN, type_path=f"{DT}/T_New"
                    ),
                    ArgumentSpec(
                        name="Value", direction=ArgumentDirection.OUT, type_path=f"{DT}/T_Val001"
                    ),
                    ArgumentSpec(
                        name="State", direction=ArgumentDirection.INOUT, type_path=f"{DT}/T_Val001"
                    ),
                ),
            ),
            OperationSpec(name="Reset"),
        ),
    )


TYPES: tuple[AnyOperation, ...] = (
    data_type("T_New", "value", base_type_path=UINT8),
    data_type(
        "T_Scaled",
        "value",
        base_type_path=UINT8,
        factor=0.5,
        offset=-40,
        unit="KiloMeterPerHour",
        lower_limit=0,
        upper_limit=250,
    ),
    data_type("T_Flag", "boolean", base_type_path=BOOLEAN),
    data_type("T_State", "enumeration", base_type_path=UINT8, enum_values=((0, "OFF"), (1, "ON"))),
    data_type("T_List", "array", element_type_path=f"{DT}/T_Scaled", array_size=8),
    data_type("T_Raw", "array", element_type_path=UINT8, array_size=4),
    data_type(
        "T_Pair",
        "record",
        record_elements=(
            RecordElementSpec(name="First", type_path=f"{DT}/T_New"),
            RecordElementSpec(name="Second", type_path=f"{DT}/T_State"),
        ),
    ),
    data_type("T_Alias", "type-reference", base_type_path=f"{DT}/T_New"),
    data_type("A_Speed", "value", "application", factor=0.01, unit=f"{DT}/Units/Volt"),
    data_type("A_Plain", "value", "application", unit="V", lower_limit=-1.5, upper_limit=1.5),
    data_type("A_Flag", "boolean", "application"),
    data_type("A_Mode", "enumeration", "application", enum_values=((1, "A"), (2, "B"))),
    data_type("A_List", "array", "application", element_type_path=f"{DT}/A_Speed", array_size=3),
    data_type(
        "A_Pair",
        "record",
        "application",
        record_elements=(
            RecordElementSpec(name="Speed", type_path=f"{DT}/A_Speed"),
            RecordElementSpec(name="Mode", type_path=f"{DT}/A_Mode"),
        ),
    ),
)
INTERFACES: tuple[AnyOperation, ...] = (
    sr_interface(
        "IfNew",
        ("Plain", f"{DT}/T_New", False),
        ("Queue", f"{DT}/T_Pair", True),
        ("Phys", f"{DT}/A_Speed", False),
    ),
    cs_interface("IfService"),
)


def add_boolean_base_type(repo: Repo) -> None:
    """The generated repo has no boolean base type: add one the way a project has it."""
    file = repo.path(BASE_FILE)
    text = file.read_text(encoding="utf-8")
    marker = "        <SW-BASE-TYPE "
    boolean = (
        "        <SW-BASE-TYPE>\n"
        "          <SHORT-NAME>boolean</SHORT-NAME>\n"
        "          <CATEGORY>FIXED_LENGTH</CATEGORY>\n"
        "          <BASE-TYPE-SIZE>8</BASE-TYPE-SIZE>\n"
        "          <BASE-TYPE-ENCODING>BOOLEAN</BASE-TYPE-ENCODING>\n"
        "        </SW-BASE-TYPE>\n"
    )
    file.write_text(text.replace(marker, boolean + marker, 1), encoding="utf-8")
    repo.reload()


def test_data_types_and_interfaces_in_existing_files(small: Repo) -> None:
    add_boolean_base_type(small)
    before_model = small.model
    before = small.snapshot()
    plan = small.plan(*INTERFACES, *TYPES)
    assert not plan.findings, plan.findings
    assert [(f.rel_path, f.action) for f in plan.files] == [
        (DT_FILE, "modify"),
        (IF_FILE, "modify"),
    ]
    assert plan.preview[0] == (
        "add sender-receiver interface /PortInterfaces/IfNew (Plain, Queue, Phys)"
    )
    assert plan.preview[3] == (
        "add implementation data type /DataTypes/T_Scaled (value), compu method CM_T_Scaled, "
        "constraint DC_T_Scaled"
    )
    outcome = small.apply_plan(plan)
    assert not outcome.findings, outcome.findings
    assert assert_only_insertions(before, small.snapshot()) == {DT_FILE, IF_FILE}

    model = small.reload()
    assert not model.findings, model.findings
    names = [op.name for op in TYPES if isinstance(op, AddDataType)]
    assert added(before_model, model) == {
        *(f"data_types:{DT}/{name}" for name in names),
        *(f"compu_methods:{CM}/CM_{n}" for n in ("T_Scaled", "T_State", "A_Speed", "A_Mode")),
        *(f"constraints:{DC}/DC_{n}" for n in ("T_Scaled", "A_Plain")),
        f"interfaces:{IF}/IfNew",
        f"interfaces:{IF}/IfService",
    }
    types = model.data_types
    for op in TYPES:
        assert isinstance(op, AddDataType)
        got = types[f"{DT}/{op.name}"]
        assert (got.level, got.category) == (op.level, op.category), op.name
    plain = types[f"{DT}/T_New"]
    assert (plain.base_type_path, plain.compu_method_path, plain.constraint_path) == (
        UINT8,
        None,
        None,
    )
    scaled = types[f"{DT}/T_Scaled"]
    assert (scaled.base_type_path, scaled.compu_method_path, scaled.constraint_path) == (
        UINT8,
        f"{CM}/CM_T_Scaled",
        f"{DC}/DC_T_Scaled",
    )
    linear = model.compu_methods[f"{CM}/CM_T_Scaled"]
    assert (linear.category, linear.factor, linear.offset, linear.unit_path) == (
        "LINEAR",
        0.5,
        -40.0,
        f"{DT}/Units/KiloMeterPerHour",
    )
    limits = model.constraints[f"{DC}/DC_T_Scaled"]
    assert (limits.lower, limits.upper) == (0.0, 250.0)
    table = model.compu_methods[f"{CM}/CM_T_State"]
    assert (table.category, table.enum_values) == ("TEXTTABLE", ((0, "OFF"), (1, "ON")))
    array = types[f"{DT}/T_List"]
    assert (array.element_type_paths, array.element_names, array.array_size) == (
        (f"{DT}/T_Scaled",),
        ("Element",),
        8,
    )
    raw = types[f"{DT}/T_Raw"]
    assert (raw.element_type_paths, raw.array_size) == ((UINT8,), 4)
    record = types[f"{DT}/T_Pair"]
    assert (record.element_names, record.element_type_paths) == (
        ("First", "Second"),
        (f"{DT}/T_New", f"{DT}/T_State"),
    )
    alias = types[f"{DT}/T_Alias"]
    assert alias.element_type_paths == (f"{DT}/T_New",)
    physical = types[f"{DT}/A_Speed"]
    assert (physical.base_type_path, physical.compu_method_path) == (None, f"{CM}/CM_A_Speed")
    assert model.compu_methods[f"{CM}/CM_A_Speed"].unit_path == f"{DT}/Units/Volt"
    unscaled = types[f"{DT}/A_Plain"]
    assert (unscaled.unit_path, unscaled.constraint_path, unscaled.compu_method_path) == (
        f"{DT}/Units/Volt",
        f"{DC}/DC_A_Plain",
        None,
    )
    assert (model.constraints[f"{DC}/DC_A_Plain"].lower, -1.5) == (-1.5, -1.5)
    app_array = types[f"{DT}/A_List"]
    assert (app_array.element_type_paths, app_array.array_size) == ((f"{DT}/A_Speed",), 3)
    app_record = types[f"{DT}/A_Pair"]
    assert (app_record.element_names, app_record.element_type_paths) == (
        ("Speed", "Mode"),
        (f"{DT}/A_Speed", f"{DT}/A_Mode"),
    )

    sender = model.interfaces[f"{IF}/IfNew"]
    assert (sender.kind, sender.is_service) == (InterfaceKind.SENDER_RECEIVER, False)
    assert sender.elements == (
        DataElement("Plain", f"{DT}/T_New", False),
        DataElement("Queue", f"{DT}/T_Pair", True),
        DataElement("Phys", f"{DT}/A_Speed", False),
    )
    service = model.interfaces[f"{IF}/IfService"]
    assert service.kind is InterfaceKind.CLIENT_SERVER
    assert service.operations == (
        Operation(
            "Get",
            (
                Argument("Id", ArgumentDirection.IN, f"{DT}/T_New"),
                Argument("Value", ArgumentDirection.OUT, f"{DT}/T_Val001"),
                Argument("State", ArgumentDirection.INOUT, f"{DT}/T_Val001"),
            ),
        ),
        Operation("Reset"),
    )
    text = small.read(IF_FILE).decode()
    assert (
        '<TYPE-TREF DEST="APPLICATION-PRIMITIVE-DATA-TYPE">/DataTypes/A_Speed</TYPE-TREF>' in text
    )
    assert text.count("<SW-IMPL-POLICY>QUEUED</SW-IMPL-POLICY>") == 1
    assert text.count("<SW-IMPL-POLICY>STANDARD</SW-IMPL-POLICY>") == 3  # the old elements


def test_new_objects_in_new_files_next_to_their_kind(small: Repo) -> None:
    small.setting(new_object_placement="new_file", standard_impl_policy=True)
    before_model = small.model
    before = small.snapshot()
    scaled = TYPES[1]
    empty = small.plan(INTERFACES[0].model_copy(update={"elements": ()}))
    assert codes_of(empty.findings) == [codes.INCOMPLETE] and not empty.files
    outcome = small.apply(
        TYPES[0], scaled, sr_interface("IfFresh", ("Plain", f"{DT}/T_New", False))
    )
    new_files = (
        "Config/Developer/DataTypes/T_New.arxml",
        "Config/Developer/DataTypes/T_Scaled.arxml",
        "Config/Developer/PortInterfaces/IfFresh.arxml",
    )
    assert outcome.written == new_files
    assert small.journal.entries == [(name, "create") for name in new_files]
    assert all(small.journal.snapshots[name] is None for name in new_files)
    # the .dcf is not updated: one warning per new file names it
    assert codes_of(outcome.findings) == [codes.DCF_NOT_UPDATED] * 3
    assert [f.file for f in outcome.findings] == list(new_files)
    assert "Synthetic.dcf was not updated" in outcome.findings[0].message
    after = small.snapshot()
    assert assert_only_insertions(before, after) == set(new_files)

    text = after[new_files[1]].decode()
    sibling = after[DT_FILE].decode()
    assert text.startswith(sibling[: sibling.index("<AR-PACKAGES>")])  # declaration and root tag
    assert text.endswith("  </AR-PACKAGES>\n</AUTOSAR>\n")
    assert "<SHORT-NAME>CM_T_Scaled</SHORT-NAME>" in text and "<DATA-CONSTR " in text
    assert "<AR-PACKAGE>\n      <SHORT-NAME>DataTypes</SHORT-NAME>" in text

    model = small.reload()
    assert [f.code for f in model.findings] == ["RDR-DCF-003"] * 3  # on disk, not in the .dcf
    assert added(before_model, model) == {
        f"data_types:{DT}/T_New",
        f"data_types:{DT}/T_Scaled",
        f"compu_methods:{CM}/CM_T_Scaled",
        f"constraints:{DC}/DC_T_Scaled",
        f"interfaces:{IF}/IfFresh",
    }
    assert model.data_types[f"{DT}/T_Scaled"].origin.file == new_files[1]
    assert model.compu_methods[f"{CM}/CM_T_Scaled"].origin.file == new_files[1]
    assert model.interfaces[f"{IF}/IfFresh"].elements == (DataElement("Plain", f"{DT}/T_New"),)
    assert "<SW-IMPL-POLICY>STANDARD</SW-IMPL-POLICY>" in after[new_files[2]].decode()


def test_other_kinds_and_incomplete_requests_are_findings(small: Repo) -> None:
    mode = INTERFACES[0].model_copy(update={"kind": InterfaceKind.MODE_SWITCH, "name": "IfMode"})
    plan = small.plan(
        mode,
        sr_interface("IfBadType", ("X", f"{DT}/Nope", False)),
        sr_interface("IfBaseType", ("X", UINT8, False)),
        sr_interface("IfSig1", ("X", f"{DT}/T_Val001", False)),
        sr_interface("T_Val001", ("X", f"{DT}/T_Val001", False), package=DT),
        data_type("T_Text", "string", base_type_path=UINT8),
        data_type("T_NoBase", "value"),
        data_type("T_WrongBase", "value", base_type_path=f"{DT}/T_Val001"),
        data_type("T_NoEnum", "enumeration", base_type_path=UINT8),
        data_type("T_NoSize", "array", element_type_path=UINT8),
        data_type("T_NoFields", "record"),
        data_type("T_Mixed", "array", element_type_path=f"{DT}/A_None", array_size=2),
        data_type("T_BadUnit", "value", base_type_path=UINT8, unit="Parsec"),
        data_type(
            "A_OfImpl", "array", "application", element_type_path=f"{DT}/T_Val001", array_size=2
        ),
        data_type("T_Enum002", "value", base_type_path=UINT8),
        data_type("T_NotBool", "boolean", base_type_path=UINT8),
        data_type("A_Ref", "type-reference", "application", base_type_path=f"{DT}/T_Val001"),
    )
    assert not plan.files and not plan.preview
    assert codes_of(plan.findings) == [
        # data types first (apply order), then interfaces
        codes.UNSUPPORTED,
        codes.INCOMPLETE,
        codes.REF_KIND,
        codes.INCOMPLETE,
        codes.INCOMPLETE,
        codes.INCOMPLETE,
        codes.REF_MISSING,
        codes.REF_MISSING,
        codes.REF_KIND,
        codes.NAME_USED,
        codes.REF_KIND,
        codes.UNSUPPORTED,
        codes.UNSUPPORTED,
        codes.REF_MISSING,
        codes.REF_KIND,
        codes.NAME_USED,
        codes.NAME_USED,
    ]
    assert "mode-switch interface is not supported yet" in plan.findings[12].message
    assert not errors([f for f in plan.findings if f.code == codes.INFO_EXISTS])


def test_existing_compu_method_is_used_and_reported(small: Repo) -> None:
    # CM_T_Val001 and DC_T_Val001 exist; a new type T_Val001 in another package would reuse them
    outcome = small.apply(
        data_type(
            "T_Val001",
            "value",
            base_type_path=UINT8,
            factor=2.0,
            lower_limit=0,
            package_path=f"{DT}/Other",
        )
    )
    assert codes_of(outcome.findings) == [codes.VALUE_IGNORED, codes.VALUE_IGNORED]
    model = small.reload()
    new = model.data_types[f"{DT}/Other/T_Val001"]
    assert new.compu_method_path == f"{CM}/CM_T_Val001"
    assert new.constraint_path == f"{DC}/DC_T_Val001"
    assert new.origin.file == DT_FILE  # nearest existing parent package


def test_data_type_map_is_written_only_when_the_setting_names_a_mapping_set(small: Repo) -> None:
    file = small.path(DT_FILE)
    text = file.read_text(encoding="utf-8")
    marker = "        <IMPLEMENTATION-DATA-TYPE "
    mapping_set = (
        "        <DATA-TYPE-MAPPING-SET>\n"
        "          <SHORT-NAME>TypeMaps</SHORT-NAME>\n"
        "        </DATA-TYPE-MAPPING-SET>\n"
    )
    file.write_text(text.replace(marker, mapping_set + marker, 1), encoding="utf-8")
    small.reload()
    request = data_type("A_Mapped", "value", "application", base_type_path=f"{DT}/T_Val001")
    small.apply(request)
    assert small.reload().mapping_sets[f"{DT}/TypeMaps"].pairs == ()

    small.setting(data_type_mapping_set=f"{DT}/TypeMaps")
    other = data_type("A_Mapped2", "value", "application", base_type_path=f"{DT}/T_Val001")
    outcome = small.apply(other)
    assert not outcome.findings
    assert small.reload().mapping_sets[f"{DT}/TypeMaps"].pairs == (
        (f"{DT}/A_Mapped2", f"{DT}/T_Val001"),
    )
    small.setting(data_type_mapping_set=f"{DT}/NoSuchSet")
    third = data_type("A_Mapped3", "value", "application", base_type_path=f"{DT}/T_Val001")
    assert codes_of(small.apply(third).findings) == [codes.VALUE_IGNORED]
