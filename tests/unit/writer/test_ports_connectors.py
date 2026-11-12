"""AddPort and AddConnector, verified by reloading the repository with the reader."""

from __future__ import annotations

from porttool.core.models import ComSpec, ConnectorEnd, ConnectorKind, PortDirection
from porttool.core.operations import AddConnector, AddPort, InitValueSpec, Route
from porttool.writer import codes
from tests.unit.writer.conftest import (
    COMPOSITION,
    CT,
    IF,
    Repo,
    added,
    assert_only_insertions,
    codes_of,
    errors,
)

SWC1 = f"{CT}/Swc1"
SWC2 = f"{CT}/Swc2"
SWC1_FILE = "Config/Developer/ComponentTypes/Swc1.arxml"
SWC2_FILE = "Config/Developer/ComponentTypes/Swc2.arxml"
COMPOSITION_FILE = "Config/Developer/ComponentTypes/EcuComposition.arxml"


def _port(swc: str, name: str, direction: str, interface: str, **inits: str | None) -> AddPort:
    return AddPort(
        id=f"port-{swc[-1]}-{name}",
        swc_path=swc,
        name=name,
        direction=PortDirection(direction),
        interface_path=f"{IF}/{interface}",
        init_values=tuple(InitValueSpec(element=k, value=v) for k, v in inits.items()),
    )


def _connector(name: str, provider: str, p_port: str, requester: str, r_port: str) -> AddConnector:
    return AddConnector(
        id=f"conn-{name}",
        composition_path=COMPOSITION,
        name=name,
        provider_prototype=provider,
        provider_port=p_port,
        requester_prototype=requester,
        requester_port=r_port,
    )


def test_plan_writes_nothing_and_previews_every_operation(small: Repo) -> None:
    before = small.snapshot()
    plan = small.plan(
        _port(SWC1, "PpNew", "P", "IfSig3", De1="7"),
        _port(SWC2, "RpNew", "R", "IfSig3", De1="7", De2=None),
        _connector("Swc1_PpNew_Swc2_RpNew", "Swc1", "PpNew", "Swc2", "RpNew"),
    )
    assert small.snapshot() == before
    assert not plan.findings
    assert [(f.rel_path, f.action) for f in plan.files] == [
        (COMPOSITION_FILE, "modify"),
        (SWC1_FILE, "modify"),
        (SWC2_FILE, "modify"),
    ]
    assert [f.summary for f in plan.files] == [
        "add connector Swc1_PpNew_Swc2_RpNew",
        "add port PpNew",
        "add port RpNew",
    ]
    assert plan.preview == (
        "Swc1: add P-port PpNew (IfSig3), init De1=7",
        "Swc2: add R-port RpNew (IfSig3), init De1=7",
        "EcuComposition: connect Swc1.PpNew -> Swc2.RpNew (Swc1_PpNew_Swc2_RpNew)",
    )


def test_ports_and_connector_are_exactly_what_the_reader_finds_after_apply(small: Repo) -> None:
    before_model = small.model
    before = small.snapshot()
    operations = (
        _port(SWC1, "PpNew", "P", "IfSig3", De1="7"),
        _port(SWC2, "RpNew", "R", "IfSig3", De1="7", De2=None),
        _port(SWC1, "PrpNew", "PR", "IfSig1", De1="[1, 2]"),
        _port(SWC2, "PpServer", "P", "IfSrv2"),
        _port(SWC1, "RpClient", "R", "IfSrv2"),
        _connector("Swc1_PpNew_Swc2_RpNew", "Swc1", "PpNew", "Swc2", "RpNew"),
        _connector("Swc1_PrpNew_Swc2_RpSig1", "Swc1", "PrpNew", "Swc2", "RpSig1"),
    )
    outcome = small.apply(*operations)
    assert not errors(outcome.findings), outcome.findings
    assert outcome.written == (COMPOSITION_FILE, SWC1_FILE, SWC2_FILE)
    assert set(outcome.applied_operation_ids) == {op.id for op in operations}
    assert small.journal.entries == [
        (COMPOSITION_FILE, "modify"),
        (SWC1_FILE, "modify"),
        (SWC2_FILE, "modify"),
    ]
    # the journal saw every file before it was changed
    assert all(small.journal.snapshots[name] == before[name] for name in outcome.written)
    assert assert_only_insertions(before, small.snapshot()) == set(outcome.written)

    model = small.reload()
    assert not model.findings, model.findings
    assert added(before_model, model) == {
        f"ports:{SWC1}/PpNew",
        f"ports:{SWC2}/RpNew",
        f"ports:{SWC1}/PrpNew",
        f"ports:{SWC2}/PpServer",
        f"ports:{SWC1}/RpClient",
        f"connectors:{COMPOSITION}/Swc1_PpNew_Swc2_RpNew",
        f"connectors:{COMPOSITION}/Swc1_PrpNew_Swc2_RpSig1",
    }
    provided = model.ports[f"{SWC1}/PpNew"]
    assert (provided.direction, provided.interface_path) == (PortDirection.P, f"{IF}/IfSig3")
    assert provided.com_specs == (ComSpec("De1", "7"), ComSpec("De2", None))
    required = model.ports[f"{SWC2}/RpNew"]
    assert (required.direction, required.interface_path) == (PortDirection.R, f"{IF}/IfSig3")
    assert required.com_specs == (ComSpec("De1", "7"), ComSpec("De2", None))
    both = model.ports[f"{SWC1}/PrpNew"]
    assert (both.direction, both.interface_path) == (PortDirection.PR, f"{IF}/IfSig1")
    assert both.com_specs == (ComSpec("De1", "[1, 2]"), ComSpec("De1", "[1, 2]"))
    server = model.ports[f"{SWC2}/PpServer"]
    assert server.com_specs == (ComSpec("Op1", None, 1),)
    client = model.ports[f"{SWC1}/RpClient"]
    assert client.com_specs == (ComSpec("Op1", None, None),)
    assert provided.origin.uuid is not None and provided.origin.uuid == provided.origin.uuid.lower()

    connector = model.connectors[f"{COMPOSITION}/Swc1_PpNew_Swc2_RpNew"]
    assert connector.kind is ConnectorKind.ASSEMBLY
    assert connector.provider == ConnectorEnd(f"{COMPOSITION}/Swc1", f"{SWC1}/PpNew")
    assert connector.requester == ConnectorEnd(f"{COMPOSITION}/Swc2", f"{SWC2}/RpNew")
    pr_connector = model.connectors[f"{COMPOSITION}/Swc1_PrpNew_Swc2_RpSig1"]
    assert pr_connector.provider == ConnectorEnd(f"{COMPOSITION}/Swc1", f"{SWC1}/PrpNew")
    text = small.read(COMPOSITION_FILE).decode()
    assert f'<TARGET-P-PORT-REF DEST="PR-PORT-PROTOTYPE">{SWC1}/PrpNew</TARGET-P-PORT-REF>' in text


def test_new_port_text_matches_the_style_of_the_file(small: Repo) -> None:
    small.writer = type(small.writer)(uuid_factory=lambda: "0000aaaa-0000-4000-8000-00000000beef")
    small.apply(_port(SWC2, "RpNew", "R", "IfSig3", De1="7"))
    text = small.read(SWC2_FILE).decode()
    assert (
        '            <R-PORT-PROTOTYPE UUID="0000aaaa-0000-4000-8000-00000000beef">\n'
        "              <SHORT-NAME>RpNew</SHORT-NAME>\n"
        "              <REQUIRED-COM-SPECS>\n"
        "                <NONQUEUED-RECEIVER-COM-SPEC>\n"
        '                  <DATA-ELEMENT-REF DEST="VARIABLE-DATA-PROTOTYPE">'
        "/PortInterfaces/IfSig3/De1</DATA-ELEMENT-REF>\n"
        "                  <ALIVE-TIMEOUT>0</ALIVE-TIMEOUT>\n"
        "                  <ENABLE-UPDATE>false</ENABLE-UPDATE>\n"
        "                  <HANDLE-NEVER-RECEIVED>false</HANDLE-NEVER-RECEIVED>\n"
        "                  <HANDLE-TIMEOUT-TYPE>NONE</HANDLE-TIMEOUT-TYPE>\n"
        "                  <INIT-VALUE>\n"
        "                    <NUMERICAL-VALUE-SPECIFICATION>\n"
        "                      <VALUE>7</VALUE>\n"
        "                    </NUMERICAL-VALUE-SPECIFICATION>\n"
        "                  </INIT-VALUE>\n"
        "                </NONQUEUED-RECEIVER-COM-SPEC>\n"
        "                <NONQUEUED-RECEIVER-COM-SPEC>\n"
        '                  <DATA-ELEMENT-REF DEST="VARIABLE-DATA-PROTOTYPE">'
        "/PortInterfaces/IfSig3/De2</DATA-ELEMENT-REF>\n"
        "                  <ALIVE-TIMEOUT>0</ALIVE-TIMEOUT>\n"
        "                  <ENABLE-UPDATE>false</ENABLE-UPDATE>\n"
        "                  <HANDLE-NEVER-RECEIVED>false</HANDLE-NEVER-RECEIVED>\n"
        "                  <HANDLE-TIMEOUT-TYPE>NONE</HANDLE-TIMEOUT-TYPE>\n"
        "                </NONQUEUED-RECEIVER-COM-SPEC>\n"
        "              </REQUIRED-COM-SPECS>\n"
        '              <REQUIRED-INTERFACE-TREF DEST="SENDER-RECEIVER-INTERFACE">'
        "/PortInterfaces/IfSig3</REQUIRED-INTERFACE-TREF>\n"
        "            </R-PORT-PROTOTYPE>\n"
        "          </PORTS>\n"
    ) in text


def test_com_spec_settings(small: Repo) -> None:
    small.setting(
        com_spec_for_every_element=False,
        receiver_com_spec_defaults=False,
        client_com_spec=False,
        uuid="upper",
    )
    outcome = small.apply(
        _port(SWC2, "RpNew", "R", "IfSig3", De1="Text"),
        _port(SWC2, "RpClient", "R", "IfSrv2"),
    )
    assert not outcome.findings
    text = small.read(SWC2_FILE).decode()
    assert "</DATA-ELEMENT-REF>\n                  <INIT-VALUE>" in text
    assert "<TEXT-VALUE-SPECIFICATION>\n                      <VALUE>Text</VALUE>" in text
    assert "CLIENT-COM-SPEC" not in text
    model = small.reload()
    assert model.ports[f"{SWC2}/RpNew"].com_specs == (ComSpec("De1", "Text"),)
    assert model.ports[f"{SWC2}/RpClient"].com_specs == ()
    uuid = model.ports[f"{SWC2}/RpNew"].origin.uuid
    assert uuid is not None and uuid == uuid.upper() != uuid.lower()


def test_only_operations_routed_to_apply_are_written(small: Repo) -> None:
    held = _port(SWC1, "PpHeld", "P", "IfSig1").model_copy(update={"route": Route.HOLD})
    model_owned = _port(f"{CT}/Swc3", "PpModel", "P", "IfSig1").model_copy(
        update={"route": Route.MODEL}
    )
    outcome = small.apply(held, model_owned, _port(SWC1, "PpNew", "P", "IfSig1"))
    assert outcome.applied_operation_ids == ("port-1-PpNew",)
    assert outcome.written == (SWC1_FILE,)
    assert not outcome.findings


def test_problems_are_findings_and_the_rest_is_applied(small: Repo) -> None:
    plan = small.plan(
        _port(SWC1, "PpNew", "P", "IfMissing"),
        _port(f"{CT}/Nope", "PpNew", "P", "IfSig1"),
        _port(SWC1, "PpBadInit", "P", "IfSig1", Nope="1"),
        _connector("C1", "Swc1", "PpNope", "Swc2", "RpSig1"),
        _connector("C2", "Ghost", "PpSig1", "Swc2", "RpSig1"),
        _connector("C3", "Swc2", "RpSig1", "Swc1", "PpSig1"),
        _port(SWC2, "RpGood", "R", "IfSig1"),
    )
    assert codes_of(plan.findings) == [
        codes.REF_MISSING,
        codes.REF_MISSING,
        codes.REF_MISSING,
        codes.REF_MISSING,
        codes.REF_MISSING,
        codes.REF_KIND,
    ]
    assert all(f.message.startswith("[op ") for f in plan.findings)
    assert [f.rel_path for f in plan.files] == [SWC2_FILE]
    assert plan.preview == ("Swc2: add R-port RpGood (IfSig1)",)
    outcome = small.apply_plan(plan)
    assert outcome.applied_operation_ids == ("port-2-RpGood",)
    assert set(added(small.model, small.load())) == {f"ports:{SWC2}/RpGood"}
