"""Model-level corner cases: references, duplicates, names, unsupported content."""

from __future__ import annotations

from porttool.core.models import ArxmlOwner, ComponentKind, PortDirection
from porttool.core.results import Severity
from porttool.reader import codes
from tests.unit.reader import samples
from tests.unit.reader.conftest import Repo, codes_of, findings_with
from tests.unit.reader.samples import (
    CT,
    DT,
    IF,
    assembly,
    composition,
    doc,
    implementation_type,
    p_port,
    package,
    prototype,
    r_port,
    sr_interface,
    swc,
)

TYPES = doc(package("DataTypes", implementation_type("uint16")))


def write_basics(repo: Repo) -> None:
    """One data type and one sender-receiver interface IfOne (element Value)."""
    repo.write("Config/Developer/Types.arxml", TYPES)
    repo.write("Config/Developer/If.arxml", doc(package("PortInterfaces", sr_interface("IfOne"))))


# ---- packages and files


def test_one_package_split_over_several_files(any_repo: Repo) -> None:
    write_basics(any_repo)
    for name in ("SwcOne", "SwcTwo", "SwcThree"):
        any_repo.write(
            f"Config/Developer/Split/{name}.arxml",
            doc(package("ComponentTypes", swc(name, ports=p_port("Pp", f"{IF}/IfOne")))),
        )
    # the same package again, this time with a sub-package
    any_repo.write(
        "Config/Developer/Split/Sub.arxml",
        doc(package("ComponentTypes", packages=package("Sub", swc("SwcDeep")))),
    )
    model = any_repo.load()
    assert model.findings == ()
    assert sorted(model.components) == [
        f"{CT}/Sub/SwcDeep",
        f"{CT}/SwcOne",
        f"{CT}/SwcThree",
        f"{CT}/SwcTwo",
    ]
    assert {c.origin.file for c in model.components.values()} == {
        "Config/Developer/Split/Sub.arxml",
        "Config/Developer/Split/SwcOne.arxml",
        "Config/Developer/Split/SwcTwo.arxml",
        "Config/Developer/Split/SwcThree.arxml",
    }


def test_component_split_over_two_files_is_merged(any_repo: Repo) -> None:
    write_basics(any_repo)
    any_repo.write(
        "Config/Developer/A_ports.arxml",
        doc(package("ComponentTypes", swc("Swc", ports=p_port("Pp", f"{IF}/IfOne")))),
    )
    behavior = (
        "<SWC-INTERNAL-BEHAVIOR><SHORT-NAME>Beh</SHORT-NAME><RUNNABLES><RUNNABLE-ENTITY>"
        "<SHORT-NAME>Run</SHORT-NAME></RUNNABLE-ENTITY></RUNNABLES></SWC-INTERNAL-BEHAVIOR>"
    )
    any_repo.write(
        "Config/Developer/B_behavior.arxml",
        doc(package("ComponentTypes", swc("Swc", behavior=behavior))),
    )
    model = any_repo.load()
    assert codes_of(model) == [codes.SPLIT_ELEMENT]
    assert model.findings[0].severity is Severity.INFO
    assert sorted(model.components) == [f"{CT}/Swc"]
    component = model.components[f"{CT}/Swc"]
    assert component.origin.file == "Config/Developer/A_ports.arxml"
    assert component.behavior_path == f"{CT}/Swc/Beh"
    assert [p.name for p in model.ports_of(component.path)] == ["Pp"]
    assert [r.origin.file for r in model.runnables_of(component.path)] == [
        "Config/Developer/B_behavior.arxml"
    ]


def test_duplicate_short_name_paths_in_two_files(any_repo: Repo) -> None:
    any_repo.write("Config/Developer/Types.arxml", TYPES)
    any_repo.write(
        "Config/Developer/A.arxml", doc(package("PortInterfaces", sr_interface("IfDup", "First")))
    )
    any_repo.write(
        "Config/Developer/B.arxml", doc(package("PortInterfaces", sr_interface("IfDup", "Second")))
    )
    model = any_repo.load()
    assert codes_of(model) == [codes.DUPLICATE_PATH]
    finding = model.findings[0]
    assert finding.severity is Severity.ERROR
    assert finding.object_path == f"{IF}/IfDup" and finding.file == "Config/Developer/B.arxml"
    assert "Config/Developer/A.arxml" in finding.message
    # the first file in path order wins, completely
    interface = model.interfaces[f"{IF}/IfDup"]
    assert interface.origin.file == "Config/Developer/A.arxml"
    assert [e.name for e in interface.elements] == ["First"]


def test_duplicate_component_is_reported_once_and_its_children_are_not_doubled(
    any_repo: Repo,
) -> None:
    write_basics(any_repo)
    component = swc("Swc", ports=p_port("Pp", f"{IF}/IfOne") + r_port("Rp", f"{IF}/IfOne"))
    any_repo.write("Config/Developer/A.arxml", doc(package("ComponentTypes", component)))
    any_repo.write("Config/Developer/Backup/A.arxml", doc(package("ComponentTypes", component)))
    model = any_repo.load()
    duplicates = findings_with(model, codes.DUPLICATE_PATH)
    assert [f.object_path for f in duplicates] == [f"{CT}/Swc"]  # not one per port
    assert codes_of(model) == [codes.DUPLICATE_PATH]
    assert len(model.components) == 1 and len(model.ports) == 2
    assert {p.origin.file for p in model.ports.values()} == {"Config/Developer/A.arxml"}


def test_duplicate_with_another_kind_drops_the_later_object_and_its_children(
    any_repo: Repo,
) -> None:
    write_basics(any_repo)
    any_repo.write(
        "Config/Developer/A.arxml",
        doc(package("ComponentTypes", swc("Thing", ports=p_port("Pp", f"{IF}/IfOne")))),
    )
    any_repo.write(
        "Config/Developer/B.arxml",
        doc(
            package(
                "ComponentTypes",
                composition("Thing", ports=r_port("RpOther", f"{IF}/IfOne")),
            )
        ),
    )
    model = any_repo.load()
    assert [f.object_path for f in findings_with(model, codes.DUPLICATE_PATH)] == [f"{CT}/Thing"]
    assert model.components[f"{CT}/Thing"].kind is ComponentKind.APPLICATION
    assert [p.name for p in model.ports_of(f"{CT}/Thing")] == ["Pp"]
    assert model.compositions == {}


def test_duplicate_inside_one_file(any_repo: Repo) -> None:
    write_basics(any_repo)
    ports = p_port("Same", f"{IF}/IfOne") + r_port("Same", f"{IF}/IfOne")
    any_repo.write("Config/Developer/A.arxml", doc(package("ComponentTypes", swc("Swc", ports))))
    model = any_repo.load()
    assert [f.object_path for f in findings_with(model, codes.DUPLICATE_PATH)] == [f"{CT}/Swc/Same"]
    port = model.find_port(f"{CT}/Swc", "Same")
    assert port is not None and port.direction is PortDirection.P


# ---- references


def test_reference_to_an_object_that_does_not_exist(any_repo: Repo) -> None:
    write_basics(any_repo)
    ports = p_port("PpGood", f"{IF}/IfOne") + r_port("RpLost", f"{IF}/IfMissing")
    any_repo.write("Config/Developer/Swc.arxml", doc(package("ComponentTypes", swc("Swc", ports))))
    any_repo.write(
        "Config/Developer/Comp.arxml",
        doc(
            package(
                "ComponentTypes",
                composition(
                    "Top",
                    prototypes=prototype("Swc", f"{CT}/Swc")
                    + prototype("Ghost", f"{CT}/NoSuchSwc"),
                    connectors=assembly(
                        "Conn", f"{CT}/Top/Swc", f"{CT}/Swc/PpGood", f"{CT}/Top/Ghost", f"{CT}/X/Rp"
                    ),
                ),
            )
        ),
    )
    model = any_repo.load()
    unresolved = findings_with(model, codes.REF_UNRESOLVED)
    assert {(f.object_path, f.file) for f in unresolved} == {
        (f"{CT}/Swc/RpLost", "Config/Developer/Swc.arxml"),
        (f"{CT}/Top/Ghost", "Config/Developer/Comp.arxml"),
        (f"{CT}/Top/Conn", "Config/Developer/Comp.arxml"),
    }
    assert all(f.severity is Severity.WARNING for f in unresolved)
    assert any(f"{IF}/IfMissing" in f.message for f in unresolved)
    assert codes_of(model).count(codes.REF_UNRESOLVED) == len(model.findings) == 3
    # the reference is kept as written, so the user can see what was meant
    lost = model.find_port(f"{CT}/Swc", "RpLost")
    assert lost is not None and lost.interface_path == f"{IF}/IfMissing"
    assert lost.interface_path not in model.interfaces
    assert model.prototypes[f"{CT}/Top/Ghost"].type_path == f"{CT}/NoSuchSwc"
    connector = model.connectors[f"{CT}/Top/Conn"]
    assert connector.requester.port_path == f"{CT}/X/Rp"
    assert connector.provider.port_path == f"{CT}/Swc/PpGood"


def test_reference_with_the_wrong_dest_type(any_repo: Repo) -> None:
    write_basics(any_repo)
    ports = (
        p_port("PpWrongDest", f"{IF}/IfOne", dest="CLIENT-SERVER-INTERFACE")
        + p_port("PpAbstractDest", f"{IF}/IfOne", dest="PORT-INTERFACE")
        + p_port("PpWrongKind", f"{DT}/uint16", dest="SENDER-RECEIVER-INTERFACE")
    )
    any_repo.write("Config/Developer/Swc.arxml", doc(package("ComponentTypes", swc("Swc", ports))))
    model = any_repo.load()
    assert sorted(codes_of(model)) == [codes.REF_DEST_MISMATCH, codes.REF_WRONG_TYPE]
    mismatch = findings_with(model, codes.REF_DEST_MISMATCH)[0]
    assert mismatch.object_path == f"{CT}/Swc/PpWrongDest"
    assert "CLIENT-SERVER-INTERFACE" in mismatch.message
    assert "SENDER-RECEIVER-INTERFACE" in mismatch.message
    wrong_kind = findings_with(model, codes.REF_WRONG_TYPE)[0]
    assert wrong_kind.object_path == f"{CT}/Swc/PpWrongKind"
    assert "IMPLEMENTATION-DATA-TYPE" in wrong_kind.message
    # a wrong DEST does not stop the reference from resolving by path
    ports_by_name = {p.name: p for p in model.ports_of(f"{CT}/Swc")}
    assert ports_by_name["PpWrongDest"].interface_path == f"{IF}/IfOne"
    assert ports_by_name["PpAbstractDest"].interface_path == f"{IF}/IfOne"
    assert len(model.ports_using(f"{IF}/IfOne")) == 2


REFERENCE_BASES = (
    "<REFERENCE-BASES>"
    "<REFERENCE-BASE><SHORT-LABEL>default</SHORT-LABEL><IS-DEFAULT>true</IS-DEFAULT>"
    "<IS-GLOBAL>false</IS-GLOBAL><BASE-IS-THIS-PACKAGE>false</BASE-IS-THIS-PACKAGE>"
    f'<PACKAGE-REF DEST="AR-PACKAGE">{IF}</PACKAGE-REF></REFERENCE-BASE>'
    "<REFERENCE-BASE><SHORT-LABEL>here</SHORT-LABEL><IS-DEFAULT>false</IS-DEFAULT>"
    "<BASE-IS-THIS-PACKAGE>true</BASE-IS-THIS-PACKAGE></REFERENCE-BASE>"
    "</REFERENCE-BASES>"
)


def relative_r_port(name: str, text: str, base: str | None = None) -> str:
    attribute = "" if base is None else f' BASE="{base}"'
    return (
        f"<R-PORT-PROTOTYPE><SHORT-NAME>{name}</SHORT-NAME>"
        f'<REQUIRED-INTERFACE-TREF DEST="SENDER-RECEIVER-INTERFACE"{attribute}>{text}'
        "</REQUIRED-INTERFACE-TREF></R-PORT-PROTOTYPE>"
    )


def test_relative_references_and_reference_bases(any_repo: Repo) -> None:
    write_basics(any_repo)
    ports = (
        relative_r_port("RpDefault", "IfOne")  # no BASE: the default base
        + relative_r_port("RpNamed", "IfOne", "default")
        + relative_r_port("RpHere", "Local/IfLocal", "here")  # base is the package itself
        + relative_r_port("RpNoSuchBase", "IfOne", "nowhere")
        + r_port("RpAbsolute", f"{IF}/IfOne")
    )
    any_repo.write(
        "Config/Developer/Swc.arxml",
        doc(
            package(
                "ComponentTypes",
                swc("Swc", ports),
                extra=REFERENCE_BASES,
                packages=package("Local", sr_interface("IfLocal"))
                # a nested package inherits the bases of the packages around it
                + package("Inner", swc("Deep", relative_r_port("RpInherited", "IfOne"))),
            )
        ),
    )
    model = any_repo.load()
    ports_by_name = {p.name: p.interface_path for p in model.ports_of(f"{CT}/Swc")}
    assert ports_by_name == {
        "RpDefault": f"{IF}/IfOne",
        "RpNamed": f"{IF}/IfOne",
        "RpHere": f"{CT}/Local/IfLocal",
        "RpNoSuchBase": None,
        "RpAbsolute": f"{IF}/IfOne",
    }
    deep = model.find_port(f"{CT}/Inner/Deep", "RpInherited")
    assert deep is not None and deep.interface_path == f"{IF}/IfOne"
    assert codes_of(model) == [codes.REF_RELATIVE_UNRESOLVED]
    finding = model.findings[0]
    assert finding.object_path == f"{CT}/Swc/RpNoSuchBase" and "'IfOne'" in finding.message
    assert len(model.ports_using(f"{IF}/IfOne")) == 4


def test_reference_base_defined_in_another_file_of_the_split_package(any_repo: Repo) -> None:
    write_basics(any_repo)
    any_repo.write(
        "Config/Developer/Bases.arxml", doc(package("ComponentTypes", extra=REFERENCE_BASES))
    )
    any_repo.write(
        "Config/Developer/Swc.arxml",
        doc(package("ComponentTypes", swc("Swc", relative_r_port("Rp", "IfOne")))),
    )
    model = any_repo.load()
    assert model.findings == ()
    port = model.find_port(f"{CT}/Swc", "Rp")
    assert port is not None and port.interface_path == f"{IF}/IfOne"


def test_relative_reference_without_any_base(any_repo: Repo) -> None:
    write_basics(any_repo)
    any_repo.write(
        "Config/Developer/Swc.arxml",
        doc(package("ComponentTypes", swc("Swc", relative_r_port("Rp", "IfOne")))),
    )
    model = any_repo.load()
    assert codes_of(model) == [codes.REF_RELATIVE_UNRESOLVED]
    port = model.find_port(f"{CT}/Swc", "Rp")
    assert port is not None and port.interface_path is None


# ---- names


def test_short_names_that_differ_only_by_letter_case(any_repo: Repo) -> None:
    write_basics(any_repo)
    any_repo.write(
        "Config/Developer/Case.arxml",
        doc(
            package(
                "ComponentTypes",
                swc("SwcCase", ports=p_port("PpLower", f"{IF}/IfOne")),
                swc("SWCCASE", ports=p_port("PpUpper", f"{IF}/IfOne")),
                composition(
                    "Top",
                    prototypes=prototype("Lower", f"{CT}/SwcCase")
                    + prototype("Upper", f"{CT}/SWCCASE"),
                ),
            )
        ),
    )
    model = any_repo.load()
    # two separate objects, never merged, each reference resolves to exactly its target
    assert [p.name for p in model.ports_of(f"{CT}/SwcCase")] == ["PpLower"]
    assert [p.name for p in model.ports_of(f"{CT}/SWCCASE")] == ["PpUpper"]
    assert model.prototypes[f"{CT}/Top/Lower"].type_path == f"{CT}/SwcCase"
    assert model.prototypes[f"{CT}/Top/Upper"].type_path == f"{CT}/SWCCASE"
    assert codes_of(model) == [codes.NAME_CASE]
    finding = model.findings[0]
    assert finding.severity is Severity.WARNING
    assert f"{CT}/SWCCASE, {CT}/SwcCase" in finding.message


def test_packages_that_differ_only_by_letter_case_are_reported_once(any_repo: Repo) -> None:
    any_repo.write(
        "Config/Developer/Case.arxml",
        doc(package("Types", swc("Swc", ports=p_port("Pp", "/x"))), package("TYPES", swc("Swc"))),
    )
    model = any_repo.load()
    assert codes_of(model).count(codes.NAME_CASE) == 1
    assert "/TYPES, /Types" in findings_with(model, codes.NAME_CASE)[0].message
    assert sorted(model.components) == ["/TYPES/Swc", "/Types/Swc"]


def test_object_without_short_name_is_skipped_with_a_finding(any_repo: Repo) -> None:
    any_repo.write(
        "Config/Developer/NoName.arxml",
        doc(
            package(
                "ComponentTypes",
                "<APPLICATION-SW-COMPONENT-TYPE><PORTS/></APPLICATION-SW-COMPONENT-TYPE>",
                swc("Named"),
            )
        ),
    )
    model = any_repo.load()
    assert codes_of(model) == [codes.NAME_MISSING]
    assert sorted(model.components) == [f"{CT}/Named"]


# ---- unsupported content


def test_variation_points_are_reported_and_nothing_is_dropped(any_repo: Repo) -> None:
    write_basics(any_repo)
    variation = (
        "<VARIATION-POINT><SHORT-LABEL>vp</SHORT-LABEL><SW-SYSCOND BINDING-TIME="
        '"PRE-COMPILE-TIME">1</SW-SYSCOND></VARIATION-POINT>'
    )
    ports = p_port("PpVariant", f"{IF}/IfOne", inner=variation) + p_port("PpPlain", f"{IF}/IfOne")
    any_repo.write(
        "Config/Developer/Swc.arxml",
        doc(
            package(
                "ComponentTypes",
                swc("Swc", ports),
                composition(
                    "Top",
                    prototypes="<SW-COMPONENT-PROTOTYPE><SHORT-NAME>Inst</SHORT-NAME>"
                    f'<TYPE-TREF DEST="APPLICATION-SW-COMPONENT-TYPE">{CT}/Swc</TYPE-TREF>'
                    f"{variation}</SW-COMPONENT-PROTOTYPE>",
                ),
            )
        ),
    )
    model = any_repo.load()
    found = findings_with(model, codes.VARIATION_POINT)
    assert [(f.object_path, f.severity) for f in found] == [
        (f"{CT}/Swc/PpVariant", Severity.WARNING),
        (f"{CT}/Top/Inst", Severity.WARNING),
    ]
    assert all("not supported" in f.message for f in found)
    assert codes_of(model) == [codes.VARIATION_POINT] * 2
    # the objects are still in the model
    assert [p.name for p in model.ports_of(f"{CT}/Swc")] == ["PpVariant", "PpPlain"]
    assert f"{CT}/Top/Inst" in model.prototypes


def test_unknown_component_kind(any_repo: Repo) -> None:
    write_basics(any_repo)
    any_repo.write(
        "Config/Developer/Future.arxml",
        doc(
            package(
                "ComponentTypes",
                swc(
                    "FutureSwc", ports=p_port("Pp", f"{IF}/IfOne"), kind="QUANTUM-SW-COMPONENT-TYPE"
                ),
                composition("Top", prototypes=prototype("Inst", f"{CT}/FutureSwc", "X")),
            )
        ),
    )
    model = any_repo.load()
    component = model.components[f"{CT}/FutureSwc"]
    assert component.kind is ComponentKind.OTHER
    assert [p.name for p in model.ports_of(component.path)] == ["Pp"]
    assert model.prototypes[f"{CT}/Top/Inst"].type_path == component.path
    kind = findings_with(model, codes.KIND_UNKNOWN)
    assert [f.object_path for f in kind] == [component.path]
    assert "QUANTUM-SW-COMPONENT-TYPE" in kind[0].message
    assert sorted(codes_of(model)) == [codes.KIND_UNKNOWN, codes.REF_DEST_MISMATCH]


def test_pass_through_connector_is_reported_not_dropped_silently(any_repo: Repo) -> None:
    write_basics(any_repo)
    connector = (
        "<PASS-THROUGH-SW-CONNECTOR><SHORT-NAME>Through</SHORT-NAME>"
        f'<PROVIDED-OUTER-PORT-REF DEST="P-PORT-PROTOTYPE">{CT}/Top/Pp</PROVIDED-OUTER-PORT-REF>'
        f'<REQUIRED-OUTER-PORT-REF DEST="R-PORT-PROTOTYPE">{CT}/Top/Rp</REQUIRED-OUTER-PORT-REF>'
        "</PASS-THROUGH-SW-CONNECTOR>"
    )
    ports = p_port("Pp", f"{IF}/IfOne") + r_port("Rp", f"{IF}/IfOne")
    any_repo.write(
        "Config/Developer/Top.arxml",
        doc(package("ComponentTypes", composition("Top", connectors=connector, ports=ports))),
    )
    model = any_repo.load()
    assert codes_of(model) == [codes.CONNECTOR_UNSUPPORTED]
    assert model.findings[0].object_path == f"{CT}/Top/Through"
    assert model.connectors == {}


def test_two_internal_behaviors_in_one_component(any_repo: Repo) -> None:
    behaviors = "".join(
        f"<SWC-INTERNAL-BEHAVIOR><SHORT-NAME>{name}</SHORT-NAME><RUNNABLES><RUNNABLE-ENTITY>"
        f"<SHORT-NAME>Run{name}</SHORT-NAME></RUNNABLE-ENTITY></RUNNABLES></SWC-INTERNAL-BEHAVIOR>"
        for name in ("BehOne", "BehTwo")
    )
    any_repo.write(
        "Config/Developer/Swc.arxml", doc(package("ComponentTypes", swc("Swc", behavior=behaviors)))
    )
    model = any_repo.load()
    assert codes_of(model) == [codes.BEHAVIOR_MULTIPLE]
    assert model.components[f"{CT}/Swc"].behavior_path == f"{CT}/Swc/BehOne"
    assert [r.name for r in model.runnables_of(f"{CT}/Swc")] == ["RunBehOne", "RunBehTwo"]


def test_queued_receive_is_detected_from_the_com_spec_when_the_element_is_missing(
    any_repo: Repo,
) -> None:
    """The interface file is not loaded: the queued receiver com spec still tells."""
    port = (
        "<R-PORT-PROTOTYPE><SHORT-NAME>RpQ</SHORT-NAME><REQUIRED-COM-SPECS>"
        "<QUEUED-RECEIVER-COM-SPEC>"
        f'<DATA-ELEMENT-REF DEST="VARIABLE-DATA-PROTOTYPE">{IF}/IfGone/Ev</DATA-ELEMENT-REF>'
        "<QUEUE-LENGTH>0x10</QUEUE-LENGTH></QUEUED-RECEIVER-COM-SPEC></REQUIRED-COM-SPECS>"
        f'<REQUIRED-INTERFACE-TREF DEST="SENDER-RECEIVER-INTERFACE">{IF}/IfGone'
        "</REQUIRED-INTERFACE-TREF></R-PORT-PROTOTYPE>"
    )
    behavior = (
        "<SWC-INTERNAL-BEHAVIOR><SHORT-NAME>Beh</SHORT-NAME><RUNNABLES><RUNNABLE-ENTITY>"
        "<SHORT-NAME>Run</SHORT-NAME><DATA-RECEIVE-POINT-BY-ARGUMENTS><VARIABLE-ACCESS>"
        "<SHORT-NAME>Recv</SHORT-NAME><ACCESSED-VARIABLE><AUTOSAR-VARIABLE-IREF>"
        f'<PORT-PROTOTYPE-REF DEST="R-PORT-PROTOTYPE">{CT}/Swc/RpQ</PORT-PROTOTYPE-REF>'
        f'<TARGET-DATA-PROTOTYPE-REF DEST="VARIABLE-DATA-PROTOTYPE">{IF}/IfGone/Ev'
        "</TARGET-DATA-PROTOTYPE-REF></AUTOSAR-VARIABLE-IREF></ACCESSED-VARIABLE>"
        "</VARIABLE-ACCESS></DATA-RECEIVE-POINT-BY-ARGUMENTS></RUNNABLE-ENTITY></RUNNABLES>"
        "</SWC-INTERNAL-BEHAVIOR>"
    )
    any_repo.write(
        "Config/Developer/Swc.arxml", doc(package("ComponentTypes", swc("Swc", port, behavior)))
    )
    model = any_repo.load()
    from porttool.core.models import AccessKind

    access = model.access_points[f"{CT}/Swc/Beh/Run/Recv"]
    assert access.kind is AccessKind.QUEUED_RECEIVE and access.target == "Ev"
    rp = model.find_port(f"{CT}/Swc", "RpQ")
    assert rp is not None and rp.com_specs[0].queue_length == 16
    assert set(codes_of(model)) == {codes.REF_UNRESOLVED}


def test_generated_service_component_file_is_locked(any_repo: Repo) -> None:
    """PENDING-DISCOVERY rule: files under a ServiceComponents folder are BSW-generated."""
    any_repo.write(
        "Config/Developer/ServiceComponents/Dem_swc.arxml",
        doc(
            package("ComponentTypes", swc("DemProxy"), swc("Dem", kind="SERVICE-SW-COMPONENT-TYPE"))
        ),
    )
    any_repo.write("Config/Developer/App.arxml", doc(package("ComponentTypes", swc("App"))))
    model = any_repo.load()
    assert model.components[f"{CT}/DemProxy"].owner is ArxmlOwner.LOCKED
    assert model.components[f"{CT}/Dem"].owner is ArxmlOwner.LOCKED
    assert model.components[f"{CT}/App"].owner is ArxmlOwner.DAVINCI


def test_sample_repo_package_is_split_over_five_files(any_repo: Repo) -> None:
    samples.write_sample_repo(any_repo.root, ecuc=False)
    model = any_repo.load()
    files = {c.origin.file for c in model.components.values() if c.path.startswith(f"{CT}/")}
    assert len(files) == 5 and model.findings == ()
