"""Small extraction helpers: numbers, value specifications, objects without a name."""

from __future__ import annotations

import math

import pytest
from lxml import etree

from porttool.reader import codes, extract
from tests.unit.reader.conftest import Repo, codes_of
from tests.unit.reader.samples import CT, IF, NS, doc, package, swc


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("10", 10),
        (" 0x1F ", 31),
        ("0b101", 5),
        ("-3", -3),
        ("8.0", 8),
        ("1e3", 1000),
        ("8.5", None),
        ("INF", None),
        ("abc", None),
        ("", None),
        ("   ", None),
        (None, None),
    ],
)
def test_parse_int(text: str | None, expected: int | None) -> None:
    assert extract.parse_int(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0.01", 0.01),
        (" 1e-3 ", 0.001),
        ("5", 5.0),
        ("0x10", 16.0),
        ("INF", math.inf),
        ("-INF", -math.inf),
        ("abc", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_float(text: str | None, expected: float | None) -> None:
    assert extract.parse_float(text) == expected


def spec(inner: str) -> etree._Element:
    return etree.fromstring(f'<HOLDER xmlns="{NS}">{inner}</HOLDER>')[0]


@pytest.mark.parametrize(
    ("xml", "expected"),
    [
        (
            "<NUMERICAL-VALUE-SPECIFICATION><VALUE> 42 </VALUE></NUMERICAL-VALUE-SPECIFICATION>",
            "42",
        ),
        ("<TEXT-VALUE-SPECIFICATION><VALUE>on</VALUE></TEXT-VALUE-SPECIFICATION>", "on"),
        ("<NUMERICAL-VALUE-SPECIFICATION/>", None),
        (
            "<CONSTANT-REFERENCE><CONSTANT-REF DEST='CONSTANT-SPECIFICATION'>/C/One</CONSTANT-REF>"
            "</CONSTANT-REFERENCE>",
            "/C/One",
        ),
        ("<ARRAY-VALUE-SPECIFICATION/>", "[]"),
        (
            "<ARRAY-VALUE-SPECIFICATION><ELEMENTS><NUMERICAL-VALUE-SPECIFICATION><VALUE>1</VALUE>"
            "</NUMERICAL-VALUE-SPECIFICATION><NUMERICAL-VALUE-SPECIFICATION/></ELEMENTS>"
            "</ARRAY-VALUE-SPECIFICATION>",
            "[1, ?]",
        ),
        ("<RECORD-VALUE-SPECIFICATION/>", "{}"),
        (
            "<APPLICATION-VALUE-SPECIFICATION><SW-VALUE-CONT><SW-VALUES-PHYS><V>1.5</V>"
            "</SW-VALUES-PHYS></SW-VALUE-CONT></APPLICATION-VALUE-SPECIFICATION>",
            "1.5",
        ),
        (
            "<APPLICATION-VALUE-SPECIFICATION><SW-VALUE-CONT><SW-VALUES-PHYS><V>1</V><VT>two</VT>"
            "</SW-VALUES-PHYS></SW-VALUE-CONT></APPLICATION-VALUE-SPECIFICATION>",
            "[1, two]",
        ),
        ("<APPLICATION-VALUE-SPECIFICATION/>", None),
        (
            "<REFERENCE-VALUE-SPECIFICATION><REFERENCE-VALUE-REF DEST='X'>/P/Var"
            "</REFERENCE-VALUE-REF></REFERENCE-VALUE-SPECIFICATION>",
            "&/P/Var",
        ),
        ("<REFERENCE-VALUE-SPECIFICATION/>", None),
        ("<NOT-AVAILABLE-VALUE-SPECIFICATION/>", "<NOT-AVAILABLE-VALUE-SPECIFICATION>"),
    ],
)
def test_value_text(xml: str, expected: str | None) -> None:
    assert extract.value_text(spec(xml)) == expected


def test_identifiable_path_reads_names_from_the_ancestors() -> None:
    root = etree.fromstring(
        f'<AUTOSAR xmlns="{NS}"><AR-PACKAGES><AR-PACKAGE><SHORT-NAME>A</SHORT-NAME><ELEMENTS>'
        "<X><SHORT-NAME> B </SHORT-NAME><LIST><Y><SHORT-NAME>C</SHORT-NAME></Y><Y/></LIST></X>"
        "</ELEMENTS></AR-PACKAGE></AR-PACKAGES></AUTOSAR>"
    )
    named, unnamed = root.iter(extract.q("Y"))
    assert extract.identifiable_path(named) == "/A/B/C"
    assert extract.identifiable_path(unnamed) is None


def test_objects_without_short_name_at_every_level(any_repo: Repo) -> None:
    """Each nameless object is skipped with a finding; its named neighbours are loaded."""
    port = (
        "<P-PORT-PROTOTYPE><PROVIDED-INTERFACE-TREF DEST='SENDER-RECEIVER-INTERFACE'>/x"
        "</PROVIDED-INTERFACE-TREF></P-PORT-PROTOTYPE>"
    )
    behavior = (
        "<SWC-INTERNAL-BEHAVIOR/>"
        "<SWC-INTERNAL-BEHAVIOR><SHORT-NAME>Beh</SHORT-NAME><EVENTS><TIMING-EVENT/></EVENTS>"
        "<RUNNABLES><RUNNABLE-ENTITY/><RUNNABLE-ENTITY><SHORT-NAME>Run</SHORT-NAME>"
        "</RUNNABLE-ENTITY></RUNNABLES></SWC-INTERNAL-BEHAVIOR>"
    )
    composition = (
        "<COMPOSITION-SW-COMPONENT-TYPE><SHORT-NAME>Top</SHORT-NAME><COMPONENTS>"
        "<SW-COMPONENT-PROTOTYPE/></COMPONENTS><CONNECTORS><ASSEMBLY-SW-CONNECTOR/>"
        "</CONNECTORS></COMPOSITION-SW-COMPONENT-TYPE>"
    )
    interface = (
        "<SENDER-RECEIVER-INTERFACE><SHORT-NAME>If</SHORT-NAME><DATA-ELEMENTS>"
        "<VARIABLE-DATA-PROTOTYPE/></DATA-ELEMENTS></SENDER-RECEIVER-INTERFACE>"
        "<CLIENT-SERVER-INTERFACE><SHORT-NAME>Cs</SHORT-NAME><OPERATIONS>"
        "<CLIENT-SERVER-OPERATION/><CLIENT-SERVER-OPERATION><SHORT-NAME>Op</SHORT-NAME>"
        "<ARGUMENTS><ARGUMENT-DATA-PROTOTYPE/></ARGUMENTS></CLIENT-SERVER-OPERATION>"
        "</OPERATIONS></CLIENT-SERVER-INTERFACE>"
        "<TRIGGER-INTERFACE><SHORT-NAME>Tr</SHORT-NAME><TRIGGERS><TRIGGER/></TRIGGERS>"
        "</TRIGGER-INTERFACE>"
        "<MODE-SWITCH-INTERFACE><SHORT-NAME>Ms</SHORT-NAME><MODE-GROUP/></MODE-SWITCH-INTERFACE>"
        "<SENDER-RECEIVER-INTERFACE/>"
    )
    others = (
        "<IMPLEMENTATION-DATA-TYPE/><APPLICATION-PRIMITIVE-DATA-TYPE/><SW-BASE-TYPE/>"
        "<COMPU-METHOD/><DATA-CONSTR/><UNIT/><DATA-TYPE-MAPPING-SET/><CONSTANT-SPECIFICATION/>"
        "<MODE-DECLARATION-GROUP/>"
        "<IMPLEMENTATION-DATA-TYPE><SHORT-NAME>Rec</SHORT-NAME><CATEGORY>STRUCTURE</CATEGORY>"
        "<SUB-ELEMENTS><IMPLEMENTATION-DATA-TYPE-ELEMENT/></SUB-ELEMENTS></IMPLEMENTATION-DATA-TYPE>"
        "<APPLICATION-RECORD-DATA-TYPE><SHORT-NAME>AppRec</SHORT-NAME><ELEMENTS>"
        "<APPLICATION-RECORD-ELEMENT/></ELEMENTS></APPLICATION-RECORD-DATA-TYPE>"
    )
    text = doc(
        package("ComponentTypes", swc("Swc", port, behavior), composition),
        package("PortInterfaces", interface),
        package("Other", others),
        "<AR-PACKAGE><ELEMENTS/></AR-PACKAGE>",
    )
    any_repo.write("Config/Developer/Nameless.arxml", text)
    model = any_repo.load()
    assert set(codes_of(model)) == {codes.NAME_MISSING}
    assert len(model.findings) == 24
    assert all(f.file == "Config/Developer/Nameless.arxml" for f in model.findings)
    assert sorted(model.components) == [f"{CT}/Swc", f"{CT}/Top"]
    assert [r.name for r in model.runnables_of(f"{CT}/Swc")] == ["Run"]
    assert sorted(model.interfaces) == [f"{IF}/Cs", f"{IF}/If", f"{IF}/Ms", f"{IF}/Tr"]
    assert sorted(model.data_types) == ["/Other/AppRec", "/Other/Rec"]
    assert model.ports == {} and model.triggers == {} and model.connectors == {}


def test_dom_and_streaming_give_identical_records_for_every_sample_file(repo: Repo) -> None:
    from porttool.reader import EcucLayout, parse
    from tests.unit.reader import samples

    samples.write_sample_repo(repo.root)
    suffixes = EcucLayout().container_suffixes
    for rel in [*samples.SAMPLE_FILES, samples.F_ECUC]:
        path = str(repo.path(rel))
        by_dom = parse.parse_file(path, rel, 10**12, suffixes, lambda: None)
        by_stream = parse.parse_file(path, rel, 0, suffixes, lambda: None)
        assert by_dom == by_stream, rel
        assert by_dom.status == "ok" and by_dom.cacheable
    ecuc = parse.parse_file(
        str(repo.path(samples.F_ECUC)), samples.F_ECUC, 0, suffixes, lambda: None
    )
    # 3 tasks and 6 event mappings are kept; the alarm and the instance containers are not
    assert len(ecuc.ecuc_containers) == 9
    assert ecuc.ecuc_containers[0][0] == "/ActiveEcuC/Os/Task_10ms"
    assert ecuc.ecuc_containers[3][0] == "/ActiveEcuC/Rte/SwcA/TMT_RunA_10ms"
