"""File layouts and file styles the writer must cope with. One named test per corner case."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from lxml import etree

from porttool.core.models import (
    AccessKind,
    DataTypeCategory,
    DataTypeLevel,
    InterfaceKind,
    PortDirection,
    TriggerKind,
)
from porttool.core.operations import (
    AddDataType,
    AddInterface,
    AddPort,
    AddServerRunnable,
    AnyOperation,
    ElementSpec,
)
from porttool.writer import codes
from tests.unit.writer.conftest import (
    CT,
    DT,
    IF,
    Repo,
    added,
    assert_only_insertions,
    codes_of,
    errors,
)
from tests.unit.writer.test_behavior import _assert_schema_order, access, trigger
from tests.unit.writer.test_ports_connectors import _port

SWC1, SWC2 = f"{CT}/Swc1", f"{CT}/Swc2"
B2 = f"{SWC2}/Swc2_InternalBehavior"
FOLDER = "Config/Developer/ComponentTypes"
SWC1_FILE = f"{FOLDER}/Swc1.arxml"
SWC2_FILE = f"{FOLDER}/Swc2.arxml"
IF_FILE = "Config/Developer/PortInterfaces/PortInterfaces.arxml"
DT_FILE = "Config/Developer/DataTypes/DataTypes.arxml"
DCF_FILE = "Config/Developer/Synthetic.dcf"

HEADER = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<AUTOSAR xmlns="http://autosar.org/schema/r4.0">\n'
    "  <AR-PACKAGES>\n"
    "    <AR-PACKAGE>\n"
    "      <SHORT-NAME>ComponentTypes</SHORT-NAME>\n"
    "      <ELEMENTS>\n"
)
FOOTER = "      </ELEMENTS>\n    </AR-PACKAGE>\n  </AR-PACKAGES>\n</AUTOSAR>\n"


def add_file(repo: Repo, name: str, body: str) -> str:
    """Write a component type file into the workspace and list it in the .dcf."""
    rel_path = f"{FOLDER}/{name}.arxml"
    repo.path(rel_path).write_text(HEADER + body + FOOTER, encoding="utf-8")
    dcf = repo.path(DCF_FILE)
    item = f"ComponentTypes\\{name}.arxml"
    entry = f'  <FILEREF><ARXML ROOTITEM="COMPONENTTYPE">{item}</ARXML></FILEREF>\n'
    dcf.write_text(dcf.read_text().replace("</DCF>", entry + "</DCF>"))
    return rel_path


def swc(name: str, *parts: str) -> str:
    return (
        "        <APPLICATION-SW-COMPONENT-TYPE>\n"
        f"          <SHORT-NAME>{name}</SHORT-NAME>\n"
        + "".join(parts)
        + "        </APPLICATION-SW-COMPONENT-TYPE>\n"
    )


PORTS = (
    "          <PORTS>\n"
    "            <R-PORT-PROTOTYPE>\n"
    "              <SHORT-NAME>RpOld</SHORT-NAME>\n"
    '              <REQUIRED-INTERFACE-TREF DEST="SENDER-RECEIVER-INTERFACE">/PortInterfaces/IfSig1'
    "</REQUIRED-INTERFACE-TREF>\n"
    "            </R-PORT-PROTOTYPE>\n"
    "          </PORTS>\n"
)


def behavior(name: str, *parts: str) -> str:
    return (
        "          <INTERNAL-BEHAVIORS>\n"
        "            <SWC-INTERNAL-BEHAVIOR>\n"
        f"              <SHORT-NAME>{name}_IB</SHORT-NAME>\n"
        + "".join(parts)
        + "            </SWC-INTERNAL-BEHAVIOR>\n"
        "          </INTERNAL-BEHAVIORS>\n"
    )


RUNNABLES = (
    "              <RUNNABLES>\n"
    "                <RUNNABLE-ENTITY>\n"
    "                  <SHORT-NAME>Run</SHORT-NAME>\n"
    "                  <MINIMUM-START-INTERVAL>0</MINIMUM-START-INTERVAL>\n"
    "                  <CAN-BE-INVOKED-CONCURRENTLY>false</CAN-BE-INVOKED-CONCURRENTLY>\n"
    "                  <SYMBOL>Run</SYMBOL>\n"
    "                </RUNNABLE-ENTITY>\n"
    "              </RUNNABLES>\n"
    "              <SUPPORTS-MULTIPLE-INSTANTIATION>false</SUPPORTS-MULTIPLE-INSTANTIATION>\n"
)


def checked_apply(repo: Repo, *operations: AnyOperation) -> set[str]:
    """Apply, require success, reload without reader findings; returns what is new."""
    before_model = repo.model
    before = repo.snapshot()
    outcome = repo.apply(*operations)
    assert not errors(outcome.findings), outcome.findings
    assert set(outcome.applied_operation_ids) == {op.id for op in operations}
    after = repo.snapshot()
    assert assert_only_insertions(before, after) == set(outcome.written)
    for name in outcome.written:
        _assert_schema_order(after[name])
    model = repo.reload()
    assert [f for f in model.findings if f.code != "RDR-DUP-002"] == [], model.findings
    return added(before_model, model)


# ---- layouts of the component type


def test_swc_split_over_two_files_ports_in_one_internal_behavior_in_another(small: Repo) -> None:
    ports_file = add_file(small, "SwcSplit_A_Ports", swc("SwcSplit", PORTS))
    behavior_file = add_file(
        small, "SwcSplit_B_Behavior", swc("SwcSplit", behavior("SwcSplit", RUNNABLES))
    )
    model = small.reload()
    path = f"{CT}/SwcSplit"
    assert model.components[path].origin.file == ports_file
    new = checked_apply(
        small,
        _port(path, "RpNew", "R", "IfSig1"),
        access(path, "Run", "RpNew", "De1", AccessKind.IMPLICIT_READ),
        trigger(path, "Run", TriggerKind.DATA_RECEIVED, port_name="RpOld", target="De1"),
    )
    assert new == {
        f"ports:{path}/RpNew",
        f"access_points:{path}/SwcSplit_IB/Run/DRA_RpNew_De1",
        f"triggers:{path}/SwcSplit_IB/DRT_Run_RpOld_De1",
    }
    assert b"RpNew</SHORT-NAME>" in small.read(ports_file)
    assert b"<PORTS>" not in small.read(behavior_file)
    assert b"DRA_RpNew_De1" in small.read(behavior_file)
    assert b"<EVENTS>" in small.read(behavior_file)


def test_swc_split_behavior_without_runnables_is_found_by_scanning(small: Repo) -> None:
    add_file(
        small,
        "SwcScan_A",
        swc(
            "SwcScan",
            PORTS.replace("R-PORT", "P-PORT")
            .replace("REQUIRED-", "PROVIDED-")
            .replace("IfSig1", "IfSrv2")
            .replace("SENDER-RECEIVER", "CLIENT-SERVER"),
        ),
    )
    behavior_file = add_file(small, "SwcScan_B", swc("SwcScan", behavior("SwcScan")))
    small.reload()
    path = f"{CT}/SwcScan"
    new = checked_apply(
        small,
        AddServerRunnable(
            id="run",
            swc_path=path,
            name="RpOld_Op1",
            symbol="S",
            port_name="RpOld",
            operation="Op1",
        ),
    )
    assert new == {f"runnables:{path}/SwcScan_IB/RpOld_Op1"}
    assert b"<RUNNABLES>" in small.read(behavior_file)


def test_swc_with_no_ports_element(small: Repo) -> None:
    file = add_file(small, "SwcBare", swc("SwcBare", behavior("SwcBare", RUNNABLES)))
    small.reload()
    path = f"{CT}/SwcBare"
    new = checked_apply(
        small, _port(path, "PpFirst", "P", "IfSig1"), _port(path, "RpSecond", "R", "IfSig3")
    )
    assert new == {f"ports:{path}/PpFirst", f"ports:{path}/RpSecond"}
    text = small.read(file).decode()
    # PORTS is created once, after SHORT-NAME and before INTERNAL-BEHAVIORS
    assert text.count("<PORTS>") == 1
    assert (
        "          <SHORT-NAME>SwcBare</SHORT-NAME>\n"
        "          <PORTS>\n"
        "            <P-PORT-PROTOTYPE>\n"
        "              <SHORT-NAME>PpFirst</SHORT-NAME>\n"
    ) in text
    assert text.index("</PORTS>") < text.index("<INTERNAL-BEHAVIORS>")
    assert "UUID" not in text  # the file has no UUIDs, so the new elements get none


def test_swc_without_internal_behavior_gives_a_finding_and_the_port_is_still_added(
    small: Repo,
) -> None:
    add_file(small, "SwcNoIb", swc("SwcNoIb", PORTS))
    small.reload()
    path = f"{CT}/SwcNoIb"
    before_model = small.model
    outcome = small.apply(
        _port(path, "RpNew", "R", "IfSig1"),
        trigger(path, "Run", TriggerKind.INIT),
        access(path, "Run", "RpNew", "De1", AccessKind.IMPLICIT_READ),
        AddServerRunnable(
            id="run", swc_path=path, name="X", symbol="X", port_name="RpNew", operation="Op1"
        ),
    )
    assert outcome.applied_operation_ids == ("port-b-RpNew",)
    # apply order: server runnable (the port is no server port), access point, trigger
    assert codes_of(outcome.findings) == [codes.REF_KIND, codes.NO_BEHAVIOR, codes.NO_BEHAVIOR]
    assert "SwcNoIb has no internal behavior" in outcome.findings[1].message
    assert added(before_model, small.reload()) == {f"ports:{path}/RpNew"}


def test_trigger_on_swc_without_internal_behavior_names_the_missing_behavior(small: Repo) -> None:
    add_file(
        small,
        "SwcSrvNoIb",
        swc(
            "SwcSrvNoIb",
            PORTS.replace("R-PORT", "P-PORT")
            .replace("REQUIRED-", "PROVIDED-")
            .replace("IfSig1", "IfSrv2")
            .replace("SENDER-RECEIVER", "CLIENT-SERVER"),
        ),
    )
    small.reload()
    path = f"{CT}/SwcSrvNoIb"
    plan = small.plan(
        AddServerRunnable(
            id="run", swc_path=path, name="X", symbol="X", port_name="RpOld", operation="Op1"
        )
    )
    assert codes_of(plan.findings) == [codes.NO_BEHAVIOR]
    assert "has no internal behavior" in plan.findings[0].message


def test_internal_behavior_without_events_and_without_the_access_container(small: Repo) -> None:
    file = add_file(small, "SwcThin", swc("SwcThin", PORTS, behavior("SwcThin", RUNNABLES)))
    small.reload()
    path = f"{CT}/SwcThin"
    new = checked_apply(
        small,
        access(path, "Run", "RpOld", "De1", AccessKind.IMPLICIT_READ),
        access(path, "Run", "RpOld", "De1", AccessKind.EXPLICIT_READ),
        trigger(path, "Run", TriggerKind.PERIODIC, period_s=0.02),
        trigger(path, "Run", TriggerKind.INIT),
    )
    assert new == {
        f"access_points:{path}/SwcThin_IB/Run/DRA_RpOld_De1",
        f"access_points:{path}/SwcThin_IB/Run/DRP_RpOld_De1",
        f"triggers:{path}/SwcThin_IB/TMT_Run",
        f"triggers:{path}/SwcThin_IB/IT_Run",
    }
    text = small.read(file).decode()
    assert text.count("<EVENTS>") == 1
    assert text.index("<EVENTS>") < text.index("<RUNNABLES>")
    assert (
        "                  <CAN-BE-INVOKED-CONCURRENTLY>false</CAN-BE-INVOKED-CONCURRENTLY>\n"
        "                  <DATA-READ-ACCESSS>\n"
    ) in text
    assert text.index("</DATA-READ-ACCESSS>") < text.index("<DATA-RECEIVE-POINT-BY-ARGUMENTS>")
    assert text.index("</DATA-RECEIVE-POINT-BY-ARGUMENTS>") < text.index("<SYMBOL>Run</SYMBOL>")


def test_container_written_as_an_empty_element_is_opened(small: Repo) -> None:
    file = add_file(small, "SwcEmpty", swc("SwcEmpty", "          <PORTS/>\n"))
    small.reload()
    path = f"{CT}/SwcEmpty"
    before = small.read(file)
    outcome = small.apply(_port(path, "PpNew", "P", "IfSig1"))
    assert not outcome.findings
    text = small.read(file).decode()
    assert "          <PORTS>\n            <P-PORT-PROTOTYPE>\n" in text
    assert "            </P-PORT-PROTOTYPE>\n          </PORTS>\n" in text
    assert len(small.read(file)) > len(before)
    assert f"{path}/PpNew" in small.reload().ports


def test_unknown_sibling_makes_the_position_uncertain_and_is_reported(small: Repo) -> None:
    add_file(small, "SwcOdd", swc("SwcOdd", "          <VENDOR-SPECIFIC>x</VENDOR-SPECIFIC>\n"))
    small.reload()
    outcome = small.apply(_port(f"{CT}/SwcOdd", "PpNew", "P", "IfSig1"))
    assert codes_of(outcome.findings) == [codes.ORDER_UNKNOWN]
    assert "<PORTS> inside <APPLICATION-SW-COMPONENT-TYPE> of SwcOdd" in outcome.findings[0].message
    assert outcome.applied_operation_ids == ("port-d-PpNew",)


# ---- new packaged objects


def _interface(name: str, package: str = IF) -> AddInterface:
    return AddInterface(
        id=f"if-{name}",
        package_path=package,
        name=name,
        kind=InterfaceKind.SENDER_RECEIVER,
        elements=(ElementSpec(name="Value", type_path=f"{DT}/T_Val001"),),
    )


def _type(name: str, package: str = DT) -> AddDataType:
    return AddDataType(
        id=f"dt-{name}",
        package_path=package,
        name=name,
        level=DataTypeLevel.IMPLEMENTATION,
        category=DataTypeCategory.VALUE,
        base_type_path=f"{DT}/BaseTypes/uint8",
    )


def test_missing_package_for_new_objects(small: Repo) -> None:
    new = checked_apply(
        small,
        _interface("IfDeep", "/Project/Shared/Interfaces"),
        _interface("IfDeeper", "/Project/Shared/Interfaces/More"),
        _type("T_Sub", f"{DT}/Project/Types"),
        _port(SWC1, "PpDeep", "P", "IfDeep").model_copy(
            update={"interface_path": "/Project/Shared/Interfaces/IfDeep"}
        ),
    )
    assert new == {
        "interfaces:/Project/Shared/Interfaces/IfDeep",
        "interfaces:/Project/Shared/Interfaces/More/IfDeeper",
        f"data_types:{DT}/Project/Types/T_Sub",
        f"ports:{SWC1}/PpDeep",
    }
    model = small.model
    # no file holds /Project: the chain goes into the file with the most interfaces
    assert model.interfaces["/Project/Shared/Interfaces/IfDeep"].origin.file == IF_FILE
    # /DataTypes exists: the missing part of the chain is created below it
    assert model.data_types[f"{DT}/Project/Types/T_Sub"].origin.file == DT_FILE
    text = small.read(IF_FILE).decode()
    assert text.count("<SHORT-NAME>Project</SHORT-NAME>") == 1
    assert text.count("<SHORT-NAME>Shared</SHORT-NAME>") == 1
    assert (
        "    <AR-PACKAGE>\n"
        "      <SHORT-NAME>Project</SHORT-NAME>\n"
        "      <AR-PACKAGES>\n"
        "        <AR-PACKAGE>\n"
        "          <SHORT-NAME>Shared</SHORT-NAME>\n"
    ) in text
    types = small.read(DT_FILE).decode()
    assert types.index("<SHORT-NAME>DataConstrs</SHORT-NAME>") < types.index(
        "<SHORT-NAME>Project</SHORT-NAME>"
    )


def test_no_file_for_a_kind_that_the_repository_does_not_have(small: Repo) -> None:
    for name in ("PortInterfaces",):
        small.path(f"Config/Developer/PortInterfaces/{name}.arxml").unlink()
    small.reload()
    plan = small.plan(_interface("IfNowhere", "/Elsewhere"))
    assert codes_of(plan.findings) == [codes.PLACE]


def test_file_name_collision_for_a_new_file_including_case_only_difference(small: Repo) -> None:
    small.setting(new_object_placement="new_file")
    before = small.snapshot()
    plan = small.plan(
        _interface("PortInterfaces"),  # PortInterfaces/PortInterfaces.arxml exists
        _interface("portinterfaces"),  # differs only by case: one file on Windows
        _interface("IfTwice"),
        _interface("IFTWICE"),  # collides with the file planned for IfTwice
        _interface("NUL"),  # a device name on Windows
        _type("datatypes"),
    )
    assert codes_of(plan.findings) == [
        codes.FILE_COLLISION,
        codes.FILE_COLLISION,
        codes.FILE_COLLISION,
        codes.FILE_COLLISION,
        codes.FILE_NAME,
    ]
    assert "differs from Config/Developer/DataTypes/DataTypes.arxml only by case" in (
        plan.findings[0].message
    )
    assert "exists already" in plan.findings[1].message
    assert "IfTwice.arxml only by case" in plan.findings[3].message
    assert [(f.rel_path, f.action) for f in plan.files] == [
        ("Config/Developer/PortInterfaces/IfTwice.arxml", "create")
    ]
    assert plan.preview == (
        "add sender-receiver interface /PortInterfaces/IfTwice (Value) in the new file "
        "Config/Developer/PortInterfaces/IfTwice.arxml",
    )
    assert small.snapshot() == before
    # a file that is on disk but not in the model (not loaded) collides too
    small.path("Config/Developer/PortInterfaces/iffresh.ARXML").write_text("<x/>")
    assert codes_of(small.plan(_interface("IfFresh")).findings) == [codes.FILE_COLLISION]


def test_new_file_appears_between_plan_and_apply(small: Repo) -> None:
    from porttool.core.errors import RepoStateError

    small.setting(new_object_placement="new_file")
    folder = "Config/Developer/PortInterfaces"
    plan = small.plan(_interface("IfRace"))
    assert not plan.findings
    # somebody creates a file of that name (other case) after the dry run: nothing is written
    small.path(f"{folder}/ifrace.arxml").write_text("<x/>")
    outcome = small.apply_plan(plan)
    assert codes_of(outcome.findings) == [codes.FILE_COLLISION]
    assert outcome.written == () and not small.journal.entries
    small.path(f"{folder}/ifrace.arxml").unlink()

    # ... or in the last moment, after the computation: the apply stops with an error
    class Journal:
        def record(self, rel_path: str, action: str) -> None:
            small.path(rel_path).write_text("<x/>")

    small.journal = Journal()  # type: ignore[assignment]
    with pytest.raises(RepoStateError, match="IfRace.arxml appeared"):
        small.apply_plan(plan)
    assert small.read(f"{folder}/IfRace.arxml") == b"<x/>"


# ---- names and file styles


def test_non_ascii_names(small: Repo) -> None:
    latin = small.path(SWC2_FILE)
    text = latin.read_text(encoding="utf-8").replace('encoding="UTF-8"', 'encoding="ISO-8859-1"')
    latin.write_bytes(text.encode("latin-1"))
    small.reload()
    new = checked_apply(
        small,
        _port(SWC1, "PpGrößeΩ", "P", "IfSig1"),  # UTF-8 file
        _port(SWC2, "RpGrößeΩ", "R", "IfSig1"),  # ISO-8859-1 file: Ω is not in that encoding
        _interface("IfÜbertragung"),
    )
    assert new == {
        f"ports:{SWC1}/PpGrößeΩ",
        f"ports:{SWC2}/RpGrößeΩ",
        f"interfaces:{IF}/IfÜbertragung",
    }
    assert "<SHORT-NAME>PpGrößeΩ</SHORT-NAME>".encode() in small.read(SWC1_FILE)
    # kept in the file's encoding; what the encoding lacks becomes a character reference
    assert "<SHORT-NAME>RpGröße&#937;</SHORT-NAME>".encode("latin-1") in small.read(SWC2_FILE)


def test_non_ascii_name_as_a_new_file_name(small: Repo) -> None:
    small.setting(new_object_placement="new_file")
    outcome = small.apply(_interface("IfÜbertragung"))
    assert outcome.written == ("Config/Developer/PortInterfaces/IfÜbertragung.arxml",)
    assert f"{IF}/IfÜbertragung" in small.reload().interfaces


STYLE_OPERATIONS: tuple[AnyOperation, ...] = (
    _port(SWC2, "RpStyle", "R", "IfSig3", De1="3"),
    access(SWC2, "Swc2_Cyclic10ms", "RpStyle", "De1", AccessKind.IMPLICIT_READ),
    trigger(SWC2, "Swc2_Cyclic10ms", TriggerKind.DATA_RECEIVED, port_name="RpStyle", target="De2"),
)
STYLE_NEW = {
    f"ports:{SWC2}/RpStyle",
    f"access_points:{B2}/Swc2_Cyclic10ms/DRA_RpStyle_De1",
    f"triggers:{B2}/DRT_Swc2_Cyclic10ms_RpStyle_De2",
}


def restyle(repo: Repo, change: Callable[[str], bytes]) -> bytes:
    """Rewrite Swc2.arxml in another style, reload, apply the style operations."""
    path = repo.path(SWC2_FILE)
    path.write_bytes(change(path.read_text(encoding="utf-8")))
    repo.reload()
    assert not repo.model.findings, repo.model.findings
    assert checked_apply(repo, *STYLE_OPERATIONS) == STYLE_NEW
    data = repo.read(SWC2_FILE)
    etree.fromstring(data)
    return data


def test_crlf_file(small: Repo) -> None:
    data = restyle(small, lambda text: text.replace("\n", "\r\n").encode())
    assert data.count(b"\r\n") == data.count(b"\n") > 200
    assert b"<SHORT-NAME>RpStyle</SHORT-NAME>\r\n" in data


def test_mixed_line_endings_use_the_one_of_the_containers_line(small: Repo) -> None:
    def mixed(text: str) -> bytes:
        # the file is LF, only the lines of <PORTS> and <EVENTS> end with CRLF
        return (
            text.replace("<PORTS>\n", "<PORTS>\r\n").replace("<EVENTS>\n", "<EVENTS>\r\n").encode()
        )

    data = restyle(small, mixed).decode()
    port = data[data.index("<R-PORT-PROTOTYPE", data.index("RpSrv2</SHORT-NAME>")) :]
    port = port[: port.index("</R-PORT-PROTOTYPE>")]
    assert "RpStyle" in port and port.count("\r\n") == port.count("\n") > 20
    event = data[data.index("<SHORT-NAME>DRT_Swc2_Cyclic10ms_RpStyle_De2") :]
    assert event.startswith("<SHORT-NAME>DRT_Swc2_Cyclic10ms_RpStyle_De2</SHORT-NAME>\r\n")
    # the access container lies on LF lines: it gets LF
    access_point = data[data.index("<SHORT-NAME>DRA_RpStyle_De1") :]
    assert access_point.startswith("<SHORT-NAME>DRA_RpStyle_De1</SHORT-NAME>\n")


def test_tabs_versus_spaces(small: Repo) -> None:
    data = restyle(small, lambda text: text.replace("  ", "\t").encode()).decode()
    assert "\n\t\t\t\t\t\t<R-PORT-PROTOTYPE UUID=" in data
    assert "\n\t\t\t\t\t\t\t<SHORT-NAME>RpStyle</SHORT-NAME>\n" in data
    assert "  <" not in data  # no line of the file is indented with spaces
    other = small.read(SWC1_FILE).decode()
    assert "\t" not in other


def test_four_space_indentation_is_followed(small: Repo) -> None:
    data = restyle(small, lambda text: text.replace("  ", "    ").encode()).decode()
    assert "\n" + " " * 24 + "<R-PORT-PROTOTYPE UUID=" in data
    assert "\n" + " " * 28 + "<SHORT-NAME>RpStyle</SHORT-NAME>\n" in data


def test_no_xml_declaration(small: Repo) -> None:
    data = restyle(small, lambda text: text.split("\n", 1)[1].encode())
    assert data.startswith(b"<AUTOSAR ")


def test_utf8_bom(small: Repo) -> None:
    data = restyle(small, lambda text: b"\xef\xbb\xbf" + text.encode())
    assert data.startswith(b"\xef\xbb\xbf<?xml") and data.count(b"\xef\xbb\xbf") == 1


@pytest.mark.parametrize(
    ("codec", "bom"), [("utf-16-le", b"\xff\xfe"), ("utf-16-be", b"\xfe\xff")], ids=["le", "be"]
)
def test_utf16(small: Repo, codec: str, bom: bytes) -> None:
    data = restyle(
        small,
        lambda text: bom + text.replace('encoding="UTF-8"', 'encoding="UTF-16"').encode(codec),
    )
    assert data.startswith(bom) and data.count(bom) == 1
    assert "<SHORT-NAME>RpStyle</SHORT-NAME>" in data[2:].decode(codec)


def test_non_default_namespace_prefix(small: Repo) -> None:
    def prefixed(text: str) -> bytes:
        text = text.replace("</", "</AR:").replace("<", "<AR:")
        text = text.replace("<AR:/AR:", "</AR:").replace("<AR:?xml", "<?xml")
        return text.replace("<AR:AUTOSAR xmlns=", "<AR:AUTOSAR xmlns:AR=").encode()

    data = restyle(small, prefixed).decode()
    assert "<AR:SHORT-NAME>RpStyle</AR:SHORT-NAME>" in data
    assert "<AR:R-PORT-PROTOTYPE UUID=" in data and "</AR:VARIABLE-ACCESS>" in data
    assert "<SHORT-NAME>" not in data and "<R-PORT" not in data


def test_comments_and_cdata_near_the_insertion_point(small: Repo) -> None:
    def decorated(text: str) -> bytes:
        text = text.replace(
            "          </PORTS>\n",
            "            <!-- </PORTS> is not here; <P-PORT-PROTOTYPE> neither -->\n"
            "            <?keep this?>\n"
            "          </PORTS>\n",
        )
        text = text.replace(
            "          <PORTS>\n",
            '          <DESC><L-2 L="EN"><![CDATA[ </PORTS> <EVENTS> ]]></L-2></DESC>\n'
            "          <PORTS>\n",
        )
        return text.replace(
            "              </EVENTS>\n", "              <!-- <EVENTS> --></EVENTS>\n"
        ).encode()

    data = restyle(small, decorated).decode()
    # the port goes after the comment and before the real end tag
    tail = data[data.index("<?keep this?>") :]
    assert tail.startswith("<?keep this?>\n            <R-PORT-PROTOTYPE UUID=")
    assert data.count("<![CDATA[ </PORTS> <EVENTS> ]]>") == 1
    # the end tag of EVENTS shares its line with a comment: the event goes before the end tag
    assert "<!-- <EVENTS> -->\n                <DATA-RECEIVED-EVENT" in data
    assert "</DATA-RECEIVED-EVENT>\n              </EVENTS>\n" in data


def test_attribute_values_containing_a_greater_than_sign(small: Repo) -> None:
    def decorated(text: str) -> bytes:
        text = text.replace("<PORTS>", "<PORTS T=\"a > b\" S='/PORTS>'>")
        return text.replace("<EVENTS>", '<EVENTS note=">>">').encode()

    data = restyle(small, decorated).decode()
    assert data.count("<PORTS T=\"a > b\" S='/PORTS>'>") == 1
    assert data.index("RpStyle</SHORT-NAME>") > data.index("RpSrv2</SHORT-NAME>")


def test_file_on_a_single_line_stays_well_formed(small: Repo) -> None:
    def squeezed(text: str) -> bytes:
        return "".join(line.strip() for line in text.split("\n")).encode()

    data = restyle(small, squeezed)
    assert data.count(b"\n") > 0  # the new elements are on lines of their own


def test_direction_enum_is_used_by_the_helper() -> None:
    port = _port(SWC1, "X", "PR", "IfSig1")
    assert isinstance(port, AddPort) and port.direction is PortDirection.PR
