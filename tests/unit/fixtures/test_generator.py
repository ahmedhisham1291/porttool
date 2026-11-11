"""Tests for the synthetic repo generator in tests/fixtures/generator."""

from __future__ import annotations

import dataclasses
import os
import time
from collections import Counter
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest
from lxml import etree

from porttool.core.config_loader import discover_paths, load_config
from porttool.core.models import (
    AccessKind,
    ArxmlOwner,
    ComponentKind,
    InterfaceKind,
    PortDirection,
    TriggerKind,
)
from tests.fixtures.generator import (
    LARGE,
    SMALL,
    FileLayout,
    GeneratorSpec,
    GroundTruth,
    generate,
    render,
)

NS = "{http://autosar.org/schema/r4.0}"
MEDIUM = GeneratorSpec(
    swc_count=24, ports_per_swc=10, connector_count=150, seed=7, locked_count=2,
    layout=FileLayout.PER_OBJECT,
)  # fmt: skip
ACCESS_TAGS = {
    "DATA-READ-ACCESSS": AccessKind.IMPLICIT_READ,
    "DATA-WRITE-ACCESSS": AccessKind.IMPLICIT_WRITE,
    "DATA-RECEIVE-POINT-BY-ARGUMENTS": AccessKind.EXPLICIT_READ,
    "DATA-SEND-POINTS": AccessKind.EXPLICIT_SEND,
    "SERVER-CALL-POINTS": AccessKind.SYNC_SERVER_CALL,
}
EVENT_TAGS = {
    "TIMING-EVENT": TriggerKind.PERIODIC,
    "INIT-EVENT": TriggerKind.INIT,
    "DATA-RECEIVED-EVENT": TriggerKind.DATA_RECEIVED,
    "OPERATION-INVOKED-EVENT": TriggerKind.OPERATION_INVOKED,
}


@pytest.fixture(autouse=True)
def _app_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))


class Scan:
    """An independent, minimal reading of ARXML files with lxml, to check the truth against."""

    def __init__(self, files: Mapping[str, bytes], names: tuple[str, ...]) -> None:
        self.paths: dict[str, str] = {}  # short-name path -> element tag
        self.references: list[tuple[str, str, str]] = []  # (tag, DEST, target path)
        self.ports: dict[str, tuple[PortDirection, str]] = {}
        self.access: dict[str, AccessKind] = {}
        self.events: dict[str, tuple[TriggerKind, str]] = {}
        self.connectors: dict[str, tuple[str, str, str, str]] = {}
        self.task_of: dict[str, tuple[str, int]] = {}
        for name in names:
            root = etree.fromstring(files[name])
            assert root.tag == f"{NS}AUTOSAR", name
            self._walk(root, "")

    def _text(self, element: etree._Element, tag: str) -> str:
        found = element.find(f"{NS}{tag}")
        assert found is not None and found.text is not None, tag
        return str(found.text)

    def _walk(self, element: etree._Element, parent: str) -> None:
        tag = etree.QName(element).localname
        short = element.find(f"{NS}SHORT-NAME")
        path = parent
        if short is not None and short.text:
            path = f"{parent}/{short.text}"
            # Packages are split over many files; everything else must be unique.
            assert tag == "AR-PACKAGE" or path not in self.paths, f"duplicate path {path}"
            self.paths[path] = tag
            self._record(element, tag, path)
        if element.get("DEST") is not None and tag != "DEFINITION-REF":
            self.references.append((tag, element.get("DEST", ""), element.text or ""))
        for child in element:
            if isinstance(child.tag, str):
                self._walk(child, path)

    def _record(self, element: etree._Element, tag: str, path: str) -> None:
        if tag in ("P-PORT-PROTOTYPE", "R-PORT-PROTOTYPE"):
            side = "PROVIDED" if tag.startswith("P") else "REQUIRED"
            direction = PortDirection.P if tag.startswith("P") else PortDirection.R
            self.ports[path] = (direction, self._text(element, f"{side}-INTERFACE-TREF"))
        elif tag in ("VARIABLE-ACCESS", "SYNCHRONOUS-SERVER-CALL-POINT"):
            parent = element.getparent()
            assert parent is not None
            self.access[path] = ACCESS_TAGS[etree.QName(parent).localname]
        elif tag in EVENT_TAGS:
            self.events[path] = (EVENT_TAGS[tag], self._text(element, "START-ON-EVENT-REF"))
        elif tag == "ASSEMBLY-SW-CONNECTOR":
            refs = [e.text or "" for e in element.iter() if e.get("DEST") is not None]
            assert len(refs) == 4
            self.connectors[path] = (refs[0], refs[1], refs[2], refs[3])
        elif tag == "ECUC-CONTAINER-VALUE":
            values = {
                (r.findtext(f"{NS}DEFINITION-REF") or "").rsplit("/", 1)[-1]: r.findtext(
                    f"{NS}VALUE-REF"
                )
                or r.findtext(f"{NS}VALUE")
                or ""
                for r in element.iterfind(f"{NS}*/{NS}*")
            }
            if "RteEventRef" in values:
                task = values["RteMappedToTaskRef"].rsplit("/", 1)[-1]
                self.task_of[values["RteEventRef"]] = (task, int(values["RtePositionInTask"]))


def check_against_xml(files: Mapping[str, bytes], truth: GroundTruth) -> Scan:
    scan = Scan(files, (*truth.arxml_files, truth.ecuc_file))
    # Every reference points at an element that exists, and DEST names its element tag.
    for tag, dest, target in scan.references:
        assert target in scan.paths, f"{tag} -> {target} does not resolve"
        assert scan.paths[target] == dest, f"{tag} DEST={dest} but {target} is {scan.paths[target]}"
    kinds = Counter(scan.paths.values())
    assert set(truth.swc_paths) == {
        path for path, tag in scan.paths.items() if tag.endswith("-SW-COMPONENT-TYPE")
    } - {c.path for c in truth.compositions}
    assert {p.path: (p.direction, p.interface_path) for p in truth.ports} == scan.ports
    assert set(truth.runnable_paths) == {
        path for path, tag in scan.paths.items() if tag == "RUNNABLE-ENTITY"
    }
    assert {a.path: a.kind for a in truth.access_points} == scan.access
    assert {t.path: (t.kind, t.runnable_path) for t in truth.triggers} == scan.events
    assert {
        c.path: (
            c.provider.prototype_path,
            c.provider.port_path,
            c.requester.prototype_path,
            c.requester.port_path,
        )
        for c in truth.connectors
    } == scan.connectors
    assert {m.event_path: (m.task, m.position) for m in truth.task_mappings} == scan.task_of
    assert {p.path for p in truth.prototypes} == {
        path for path, tag in scan.paths.items() if tag == "SW-COMPONENT-PROTOTYPE"
    }
    assert kinds["SENDER-RECEIVER-INTERFACE"] + kinds["CLIENT-SERVER-INTERFACE"] == len(
        truth.interfaces
    )
    assert kinds["IMPLEMENTATION-DATA-TYPE"] == len(truth.data_types)
    assert kinds["COMPU-METHOD"] == len(truth.compu_methods)
    assert kinds["DATA-CONSTR"] == len(truth.constraints)
    assert kinds["SW-BASE-TYPE"] == len(truth.base_types) and kinds["UNIT"] == len(truth.units)
    for path in (*truth.port_paths, *truth.trigger_paths, *truth.connector_paths):
        assert path in scan.paths
    return scan


def check_consistency(truth: GroundTruth) -> None:
    """The truth agrees with itself and with the Model contract."""
    model = truth.to_model()
    assert len(model.ports) == len(truth.ports) == truth.counts["ports"]
    assert len(model.connectors) == len(truth.connectors) == truth.spec.connector_count
    assert len(model.access_points) == len(truth.access_points)
    assert len(model.triggers) == len(truth.triggers)
    unconnected = sorted(instance.key for instance in model.unconnected())
    assert unconnected == list(truth.unconnected)
    assert truth.counts["unconnected"] == len(unconnected)
    for connector in truth.connectors:
        provider = model.ports[connector.provider.port_path or ""]
        requester = model.ports[connector.requester.port_path or ""]
        assert provider.direction is PortDirection.P and requester.direction is PortDirection.R
        assert provider.interface_path == requester.interface_path
        assert provider.swc_path != requester.swc_path
        assert len(model.connectors_at(connector.requester.prototype_path, requester.path)) == 1
    for access in truth.access_points:
        port = model.ports[access.port_path or ""]
        interface = model.interfaces[port.interface_path or ""]
        names = [e.name for e in interface.elements] + [o.name for o in interface.operations]
        assert access.target in names and access.swc_path == port.swc_path
        assert access.runnable_path in model.runnables
    assert {m.task for m in truth.task_mappings} <= set(truth.tasks)
    mapped = {m.event_path for m in truth.task_mappings}
    for trigger in truth.triggers:
        assert (trigger.path in mapped) == (trigger.kind is not TriggerKind.OPERATION_INVOKED)
    assert len(set(truth.port_paths)) == len(truth.ports)
    assert len(set(truth.access_point_paths)) == len(truth.access_points)


# --------------------------------------------------------------------------- small preset


def test_small_preset_has_three_swcs_and_one_model_owned(tmp_path: Path) -> None:
    truth = generate(tmp_path / "repo", SMALL)
    counts = truth.counts
    assert counts["swcs"] == 3 and counts["model_owned_swcs"] == 1 and counts["locked_swcs"] == 0
    assert counts["ports"] == 12 and counts["connectors"] == 5
    assert counts["p_ports"] + counts["r_ports"] == 12
    assert counts["prototypes"] == 3 and counts["data_types"] == 4
    assert counts["unconnected"] == len(truth.unconnected) > 0
    assert [c.owner for c in truth.components].count(ArxmlOwner.MODEL) == 1
    assert truth.components[-1].kind is ComponentKind.COMPOSITION
    assert {i.kind for i in truth.interfaces} == {
        InterfaceKind.SENDER_RECEIVER,
        InterfaceKind.CLIENT_SERVER,
    }
    assert truth.repo_root == tmp_path / "repo"
    on_disk = sorted(
        path.relative_to(truth.repo_root).as_posix()
        for path in truth.repo_root.rglob("*")
        if path.is_file()
    )
    assert on_disk == list(truth.files)
    assert truth.total_bytes == sum((truth.repo_root / name).stat().st_size for name in on_disk)
    files = {name: (truth.repo_root / name).read_bytes() for name in truth.files}
    check_against_xml(files, truth)
    check_consistency(truth)
    model_swc = truth.model_owned_swcs[0]
    export = truth.repo_root / "Models" / model_swc / "arxml" / f"{model_swc}.arxml"
    workspace = truth.repo_root / truth.arxml_root / "ComponentTypes" / f"{model_swc}.arxml"
    assert export.read_bytes() == workspace.read_bytes()


def test_small_preset_covers_every_generated_kind() -> None:
    _, truth = render(SMALL)
    assert {a.kind for a in truth.access_points} >= {AccessKind.SYNC_SERVER_CALL}
    assert {t.kind for t in truth.triggers} >= {
        TriggerKind.INIT,
        TriggerKind.PERIODIC,
        TriggerKind.OPERATION_INVOKED,
    }
    assert len({d.category for d in truth.data_types}) == 4
    assert truth.tasks == ("Task_Init", "Task_10ms", "Task_100ms", "Task_Event")


# --------------------------------------------------------------------------- determinism


def test_generator_determinism_same_seed_gives_identical_bytes(tmp_path: Path) -> None:
    first = generate(tmp_path / "one", MEDIUM)
    second = generate(tmp_path / "two", MEDIUM)
    assert first.files == second.files and len(first.files) > 50
    for name in first.files:
        assert (tmp_path / "one" / name).read_bytes() == (tmp_path / "two" / name).read_bytes()
    assert dataclasses.replace(first, repo_root=None) == dataclasses.replace(second, repo_root=None)
    in_memory, _ = render(MEDIUM)
    assert (
        in_memory["Config/ECUC/Synthetic_ecuc.arxml"]
        == (tmp_path / "one" / "Config/ECUC/Synthetic_ecuc.arxml").read_bytes()
    )


def test_another_seed_gives_another_repo() -> None:
    files, truth = render(MEDIUM)
    other_files, other = render(dataclasses.replace(MEDIUM, seed=8))
    assert truth.counts["ports"] == other.counts["ports"]
    assert truth.connector_paths != other.connector_paths
    assert files != other_files


def test_file_layout_changes_the_files_not_the_model() -> None:
    per_object_files, per_object = render(MEDIUM)
    grouped_files, grouped = render(dataclasses.replace(MEDIUM, layout=FileLayout.GROUPED))
    assert len(per_object.arxml_files) > len(grouped.arxml_files)
    assert f"{grouped.arxml_root}/PortInterfaces/PortInterfaces.arxml" in grouped_files
    assert f"{grouped.arxml_root}/DataTypes/DataTypes.arxml" in grouped_files
    assert per_object.port_paths == grouped.port_paths
    assert per_object.connector_paths == grouped.connector_paths
    assert per_object.access_point_paths == grouped.access_point_paths
    assert per_object.unconnected == grouped.unconnected
    check_against_xml(grouped_files, grouped)
    check_against_xml(per_object_files, per_object)


# --------------------------------------------------------------------------- content


def test_medium_repo_matches_its_ground_truth() -> None:
    files, truth = render(MEDIUM)
    scan = check_against_xml(files, truth)
    check_consistency(truth)
    assert truth.counts["swcs"] == 24 and truth.counts["ports"] == 240
    assert truth.counts["connectors"] == 150 and truth.counts["locked_swcs"] == 2
    assert truth.counts["model_owned_swcs"] == 8
    kinds = Counter(c.kind for c in truth.components)
    assert kinds[ComponentKind.SENSOR_ACTUATOR] == 1
    assert kinds[ComponentKind.COMPLEX_DEVICE_DRIVER] == 1
    assert {a.kind for a in truth.access_points} == set(ACCESS_TAGS.values())
    assert {t.kind for t in truth.triggers} == set(EVENT_TAGS.values())
    # Every file is UTF-8 XML in the AUTOSAR namespace with the units' special characters.
    units = files[f"{truth.arxml_root}/DataTypes/Units.arxml"].decode("utf-8")
    assert "<DISPLAY-NAME>°C</DISPLAY-NAME>" in units
    assert len(scan.paths) > 1000


def test_generation_without_uuids() -> None:
    files, truth = render(dataclasses.replace(SMALL, with_uuids=False))
    assert not any(b"UUID=" in data for data in files.values())
    check_against_xml(files, truth)


def test_repo_without_connectors() -> None:
    files, truth = render(GeneratorSpec(swc_count=1, ports_per_swc=3, connector_count=0))
    assert truth.connectors == () and len(truth.unconnected) == 3
    check_against_xml(files, truth)
    check_consistency(truth)


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        (GeneratorSpec(swc_count=0), "swc_count"),
        (GeneratorSpec(swc_count=1, connector_count=1), "at least two SWCs"),
        (GeneratorSpec(swc_count=3, ports_per_swc=2, connector_count=6), "smaller than the number"),
        (GeneratorSpec(swc_count=5, port_count=3), "at least one port"),
        (GeneratorSpec(model_owned_share=1.5), "model_owned_share"),
        (GeneratorSpec(locked_count=3), "locked_count"),
        (GeneratorSpec(project_name="not a name"), "project_name"),
    ],
)
def test_impossible_specs_are_refused(spec: GeneratorSpec, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        render(spec)


def test_generate_refuses_a_non_empty_folder(tmp_path: Path) -> None:
    (tmp_path / "keep.txt").write_text("x")
    with pytest.raises(FileExistsError):
        generate(tmp_path, SMALL)
    assert os.listdir(tmp_path) == ["keep.txt"]


# --------------------------------------------------------------------------- with the config loader


def test_generated_repo_loads_with_the_config_loader(tmp_path: Path) -> None:
    truth = generate(tmp_path / "repo", MEDIUM)
    assert truth.repo_root is not None
    loaded = load_config(truth.repo_root)
    assert loaded.findings == ()
    config = loaded.config
    assert config.arxml_roots == (truth.repo_root / truth.arxml_root,)
    assert config.ecuc_file == truth.repo_root / truth.ecuc_file
    assert config.dcf_file == truth.repo_root / truth.dcf_file
    assert config.locked_swcs == set(truth.locked_swcs)
    for component in truth.components[:-1]:
        assert config.owner_of(component.name) is component.owner
        assert config.team_of(component.name) == component.team
    found = discover_paths(truth.repo_root)
    assert found.dpa_files == (truth.repo_root / truth.dpa_file,)
    assert found.dcf_files == (truth.repo_root / truth.dcf_file,)
    assert found.ecuc_files == (truth.repo_root / truth.ecuc_file,)
    assert found.arxml_roots == config.arxml_roots
    assert found.gen_data_dirs == (config.gen_data,)


# --------------------------------------------------------------------------- large preset


@pytest.fixture(scope="module")
def large_repo(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[GroundTruth, float]]:
    target = tmp_path_factory.mktemp("large") / "repo"
    begin = time.perf_counter()
    truth = generate(target, LARGE)
    yield truth, time.perf_counter() - begin


def test_large_preset_generates_in_under_60_seconds(
    large_repo: tuple[GroundTruth, float], capsys: pytest.CaptureFixture[str]
) -> None:
    truth, seconds = large_repo
    with capsys.disabled():
        print(
            f"\n[generator] large preset: {seconds:.2f} s, {truth.counts['files']} files "
            f"({truth.counts['arxml_files']} workspace ARXML), "
            f"{truth.total_bytes / 1e6:.1f} MB"
        )
    assert seconds < 60.0
    counts = truth.counts
    assert counts["swcs"] == 300
    assert counts["ports"] == 20_000
    assert counts["connectors"] == 15_000
    assert 1_900 <= counts["files"] <= 2_100  # "about 2,000 files"
    assert counts["model_owned_swcs"] == 60 and counts["locked_swcs"] == 5
    assert counts["interfaces"] == 1_400 and counts["data_types"] == 290
    assert truth.repo_root is not None
    assert sum(1 for _ in truth.repo_root.rglob("*.arxml")) == counts["arxml_files"] + 1 + 60


def test_large_preset_matches_its_ground_truth(large_repo: tuple[GroundTruth, float]) -> None:
    truth, _ = large_repo
    assert truth.repo_root is not None
    files = {
        name: (truth.repo_root / name).read_bytes()
        for name in (*truth.arxml_files, truth.ecuc_file)
    }
    check_against_xml(files, truth)
    check_consistency(truth)


@pytest.mark.skipif(
    "PORTTOOL_AUTOSAR_XSD" not in os.environ,
    reason="set PORTTOOL_AUTOSAR_XSD to an AUTOSAR 4.x schema file to validate the fixture",
)
def test_generated_arxml_validates_against_the_autosar_schema() -> None:
    schema = etree.XMLSchema(etree.parse(os.environ["PORTTOOL_AUTOSAR_XSD"]))
    files, truth = render(MEDIUM)
    for name in (*truth.arxml_files, truth.ecuc_file):
        document = etree.fromstring(files[name])
        assert schema.validate(document), f"{name}: {schema.error_log.last_error}"
