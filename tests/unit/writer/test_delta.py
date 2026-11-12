"""build_delta: one standalone ARXML with every touched element, complete."""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any

from lxml import etree

from porttool.core.operations import ChangeSet, Route
from porttool.core.results import Finding, Severity
from porttool.writer import ArxmlWriter, codes
from tests.unit.writer.conftest import CT, DT, IF, Repo, codes_of
from tests.unit.writer.test_corner_cases import PORTS, RUNNABLES, add_file, behavior, swc
from tests.unit.writer.test_ports_connectors import _port
from tests.unit.writer.test_small_one_of_each import ONE_OF_EACH

NS = "{http://autosar.org/schema/r4.0}"
SWC3 = f"{CT}/Swc3"


def _counting_writer() -> ArxmlWriter:
    counter = itertools.count(1)
    return ArxmlWriter(uuid_factory=lambda: f"00000000-0000-4000-8000-{next(counter):012d}")


def _elements(data: bytes) -> dict[str, Any]:
    """Path -> element for everything inside ELEMENTS of every package of an ARXML text."""
    found: dict[str, Any] = {}
    root = etree.fromstring(data, etree.XMLParser(remove_blank_text=True))

    def walk(holder: Any, prefix: str) -> None:
        for package in holder.findall(f"{NS}AR-PACKAGE"):
            path = f"{prefix}/{package.findtext(f'{NS}SHORT-NAME')}"
            elements = package.find(f"{NS}ELEMENTS")
            for item in elements if elements is not None else ():
                found[f"{path}/{item.findtext(f'{NS}SHORT-NAME')}"] = item
            inner = package.find(f"{NS}AR-PACKAGES")
            if inner is not None:
                walk(inner, path)

    walk(root.find(f"{NS}AR-PACKAGES"), "")
    return found


def _delta(repo: Repo, *operations: Any, name: str = "delta.arxml") -> tuple[Path, list[Finding]]:
    target = repo.root.parent / "out" / name
    findings = repo.writer.build_delta(
        repo.model, repo.config, ChangeSet(operations=operations), target
    )
    return target, list(findings)


def test_delta_holds_every_touched_element_complete_like_a_real_apply(small: Repo) -> None:
    small.writer = _counting_writer()
    before = small.snapshot()
    target, findings = _delta(small, *ONE_OF_EACH)
    assert codes_of(findings) == [codes.INFO_DELTA]
    assert "Delta with 7 element(s) written" in findings[0].message
    assert small.snapshot() == before  # the workspace is not touched
    data = target.read_bytes()
    assert data.startswith(b'<?xml version="1.0" encoding="UTF-8"?>\n<AUTOSAR xmlns=')
    delta = _elements(data)
    assert set(delta) == {
        f"{DT}/T_New",
        f"{DT}/DataConstrs/DC_T_New",
        f"{IF}/IfNew",
        f"{IF}/IfSvc",
        f"{CT}/Swc1",
        f"{CT}/Swc2",
        f"{CT}/EcuComposition",
    }
    # original UUIDs are kept, old and new content is there
    swc1 = delta[f"{CT}/Swc1"]
    assert swc1.get("UUID") == small.model.components[f"{CT}/Swc1"].origin.uuid
    names = [port.findtext(f"{NS}SHORT-NAME") for port in swc1.find(f"{NS}PORTS")]
    assert names == ["PpSrv2", "PpSig1", "PpSig3", "RpSrv2", "PpNew", "PpSvc"]
    old_port = small.model.ports[f"{CT}/Swc1/PpSig1"]
    assert swc1.find(f"{NS}PORTS")[1].get("UUID") == old_port.origin.uuid

    # the same change applied for real gives exactly these elements
    plan = small.plan(*ONE_OF_EACH)
    small.writer = _counting_writer()  # same UUIDs as in the delta
    small.apply_plan(plan)
    applied: dict[str, Any] = {}
    for name, content in small.snapshot().items():
        if name.startswith("Config/Developer/") and name.endswith(".arxml"):
            applied.update(_elements(content))
    for path, item in delta.items():
        assert etree.tostring(item, method="c14n") == etree.tostring(
            applied[path], method="c14n"
        ), path
    # same indentation as in the workspace file, so a text diff of the two is quiet
    workspace = small.read("Config/Developer/ComponentTypes/Swc1.arxml").decode()
    end_tag = "</APPLICATION-SW-COMPONENT-TYPE>"
    start = workspace.index("        <APPLICATION-SW-COMPONENT-TYPE")
    assert workspace[start : workspace.index(end_tag) + len(end_tag)] in data.decode()


def test_delta_takes_operations_of_any_route_and_ignores_owners(small: Repo) -> None:
    for_owner = _port(SWC3, "PpForModel", "P", "IfSig1").model_copy(update={"route": Route.MODEL})
    held = _port(f"{CT}/Swc1", "PpHeld", "P", "IfSig1").model_copy(update={"route": Route.HOLD})
    before = small.snapshot()
    target, findings = _delta(small, for_owner, held)
    assert codes_of(findings) == [codes.INFO_DELTA]
    delta = _elements(target.read_bytes())
    assert set(delta) == {SWC3, f"{CT}/Swc1"}
    ports = [p.findtext(f"{NS}SHORT-NAME") for p in delta[SWC3].find(f"{NS}PORTS")]
    assert ports[-1] == "PpForModel" and len(ports) == 5
    assert delta[SWC3].find(f"{NS}INTERNAL-BEHAVIORS") is not None  # the whole element
    assert small.snapshot() == before
    # apply would refuse the same operation on the model-owned component
    plan = small.plan(for_owner.model_copy(update={"route": Route.APPLY}))
    assert codes_of(plan.findings) == [codes.OWNER]


def test_delta_reports_problems_and_writes_the_rest(small: Repo) -> None:
    target, findings = _delta(
        small, _port(SWC3, "PpGood", "P", "IfSig1"), _port(SWC3, "PpBad", "P", "IfMissing")
    )
    assert codes_of(findings) == [codes.REF_MISSING, codes.INFO_DELTA]
    assert findings[0].severity is Severity.ERROR
    text = target.read_text(encoding="utf-8")
    assert "PpGood" in text and "PpBad" not in text


def test_delta_without_any_change_is_not_written(small: Repo) -> None:
    target, findings = _delta(small, _port(SWC3, "PpSig1", "P", "IfSig1"))  # exists already
    assert codes_of(findings) == [codes.INFO_EXISTS, codes.INFO_DELTA]
    assert "was not written" in findings[1].message and not target.exists()
    target, findings = _delta(small)
    assert codes_of(findings) == [codes.INFO_DELTA] and not target.exists()


def test_delta_ignores_the_new_file_setting(small: Repo) -> None:
    small.setting(new_object_placement="new_file")
    target, findings = _delta(small, ONE_OF_EACH[-1], ONE_OF_EACH[-3])
    assert codes_of(findings) == [codes.INFO_DELTA]
    assert set(_elements(target.read_bytes())) == {
        f"{DT}/T_New",
        f"{DT}/DataConstrs/DC_T_New",
        f"{IF}/IfNew",
    }
    assert not small.path("Config/Developer/DataTypes/T_New.arxml").exists()


def test_delta_of_a_split_component_holds_every_part_and_says_so(small: Repo) -> None:
    add_file(small, "SwcSplit_A_Ports", swc("SwcSplit", PORTS))
    add_file(small, "SwcSplit_B_Behavior", swc("SwcSplit", behavior("SwcSplit", RUNNABLES)))
    small.reload()
    target, findings = _delta(small, _port(f"{CT}/SwcSplit", "RpNew", "R", "IfSig1"))
    assert codes_of(findings) == [codes.DELTA_SPLIT, codes.INFO_DELTA]
    assert findings[0].severity is Severity.WARNING and "2 files" in findings[0].message
    text = target.read_text(encoding="utf-8")
    assert text.count("<SHORT-NAME>SwcSplit</SHORT-NAME>") == 2
    assert "RpNew" in text and "<SHORT-NAME>Run</SHORT-NAME>" in text


def test_delta_follows_the_style_of_the_source_and_declares_other_prefixes(small: Repo) -> None:
    path = small.path("Config/Developer/ComponentTypes/Swc2.arxml")
    text = path.read_text(encoding="utf-8")
    text = text.replace("</", "</AR:").replace("<", "<AR:")
    text = text.replace("<AR:/AR:", "</AR:").replace("<AR:?xml", "<?xml")
    text = text.replace("<AR:AUTOSAR xmlns=", "<AR:AUTOSAR xmlns:AR=")
    path.write_bytes(text.replace("\n", "\r\n").encode())
    other = small.path("Config/Developer/ComponentTypes/Swc1.arxml")
    other.write_bytes(other.read_bytes().replace(b"\n", b"\r\n"))
    small.reload()
    target, findings = _delta(
        small,
        _port(f"{CT}/Swc1", "PpNew", "P", "IfSig1"),
        _port(f"{CT}/Swc2", "RpNew", "R", "IfSig1"),
    )
    assert codes_of(findings) == [codes.INFO_DELTA]
    data = target.read_bytes()
    assert data.count(b"\r\n") == data.count(b"\n")  # CRLF like the first source file
    assert b'xmlns:AR="http://autosar.org/schema/r4.0"' in data.split(b"\r\n")[1]
    delta = _elements(data)  # both elements are in the AUTOSAR namespace
    assert set(delta) == {f"{CT}/Swc1", f"{CT}/Swc2"}
    assert b"<AR:SHORT-NAME>RpNew</AR:SHORT-NAME>" in data
    assert b"<SHORT-NAME>PpNew</SHORT-NAME>" in data


def test_delta_that_cannot_be_written_is_a_finding(small: Repo) -> None:
    blocker = small.root.parent / "blocker"
    blocker.write_text("a file where a folder is needed")
    findings = small.writer.build_delta(
        small.model,
        small.config,
        ChangeSet(operations=(_port(SWC3, "PpNew", "P", "IfSig1"),)),
        blocker / "delta.arxml",
    )
    assert codes_of(findings) == [codes.DELTA_WRITE]
    assert findings[0].severity is Severity.ERROR
