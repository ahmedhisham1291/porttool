"""The click commands ``load`` and ``list``, and the plain-text report helpers."""

from __future__ import annotations

from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from porttool.core.config import ProjectConfig
from porttool.core.errors import ArxmlParseError
from porttool.core.models import Model
from porttool.reader import cli, load_blocking, register_cli, report
from tests.helpers.sample_model import build_sample_model
from tests.unit.reader import samples


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    samples.write_sample_repo(root)
    return root


def app(config: ProjectConfig | None = None) -> click.Group:
    @click.group()
    @click.pass_context
    def porttool(context: click.Context, /) -> None:
        if config is not None:
            context.obj = config

    register_cli(porttool)
    return porttool


def run(arguments: list[str], config: ProjectConfig | None = None) -> click.testing.Result:
    return CliRunner().invoke(app(config), arguments, catch_exceptions=False)


def repo_arguments(root: Path, tmp_path: Path) -> list[str]:
    return [
        "--repo",
        str(root),
        "--arxml-root",
        "Config/Developer",
        "--ecuc",
        "Config/ECUC/Ecu_ecuc.arxml",
        "--cache-dir",
        str(tmp_path / "cli-cache"),
    ]


def test_register_cli_adds_load_and_list() -> None:
    group = app()
    assert sorted(group.commands) == ["list", "load"]
    listing = group.commands["list"]
    assert isinstance(listing, click.Group)
    assert sorted(listing.commands) == ["connectors", "ports", "swc", "unconnected"]


def test_load_prints_a_summary_and_exits_with_0(repo_root: Path, tmp_path: Path) -> None:
    result = run(["load", *repo_arguments(repo_root, tmp_path)])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0] == "Components: 12 (2 compositions)"
    assert "Ports: 14" in lines
    assert "Interfaces: 8" in lines
    assert "Runnables: 5, access points: 9, triggers: 8" in lines
    assert "Component prototypes: 6, connectors: 6, unconnected port instances: 9" in lines
    assert "OS tasks: 3, mapped events: 3" in lines
    assert "Findings: 0 errors, 0 warnings, 0 notes" in lines
    assert any(line.startswith("Files: 8 (") and "parsed 8, from cache 0" in line for line in lines)
    assert "Schema: AUTOSAR_4-2-2.xsd" in lines
    warm = run(["load", *repo_arguments(repo_root, tmp_path)])
    assert "parsed 0, from cache 8" in warm.output
    cold_again = run(["load", *repo_arguments(repo_root, tmp_path), "--no-cache"])
    assert "parsed 8, from cache 0" in cold_again.output


def test_load_exits_with_1_when_the_model_has_error_findings(
    repo_root: Path, tmp_path: Path
) -> None:
    (repo_root / "Config" / "Developer" / "Broken.arxml").write_text("<AUTOSAR")
    result = run(["load", *repo_arguments(repo_root, tmp_path)])
    assert result.exit_code == 1
    assert "Findings: 1 errors, 0 warnings, 0 notes" in result.output
    assert "ERROR   RDR-PARSE-001 Config/Developer/Broken.arxml:" in result.output
    quiet = run(["load", *repo_arguments(repo_root, tmp_path), "--max-findings", "0"])
    assert quiet.exit_code == 1 and "RDR-PARSE-001" not in quiet.output


def test_porttool_error_exits_with_2_and_prints_code_and_message(tmp_path: Path) -> None:
    result = CliRunner().invoke(app(), ["load"])
    assert result.exit_code == 2
    assert result.output.startswith("[PT-E100] No project is configured.")
    assert "Traceback" not in result.output
    missing = CliRunner().invoke(app(), ["list", "swc", "--repo", str(tmp_path / "nope")])
    assert missing.exit_code == 2 and missing.output.startswith("[PT-E100] The repository folder")
    # with a project configuration from the context, the repository options need --repo
    inherited = ProjectConfig(repo_root=tmp_path)
    orphan = CliRunner().invoke(app(inherited), ["list", "ports", "--ecuc", "x.arxml"])
    assert orphan.exit_code == 2 and "need --repo" in orphan.output


def test_any_porttool_error_during_the_load_exits_with_2(
    repo_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object, **kwargs: object) -> object:
        raise ArxmlParseError("The repository could not be read.")

    monkeypatch.setattr(cli, "load_blocking", fail)
    result = CliRunner().invoke(app(), ["load", *repo_arguments(repo_root, tmp_path)])
    assert result.exit_code == 2
    assert result.output.strip() == "[PT-E200] The repository could not be read."


def test_configuration_from_the_click_context(repo_root: Path, tmp_path: Path) -> None:
    config = ProjectConfig(
        repo_root=repo_root,
        arxml_roots=(repo_root / "Config" / "Developer",),
        cache_dir=tmp_path / "ctx-cache",
    )
    result = run(["list", "swc", "--format", "tsv"], config)
    assert result.exit_code == 0
    rows = [line.split("\t") for line in result.output.splitlines()]
    assert rows[0] == list(report.SWC_HEADERS)
    assert [row[0] for row in rows[1:]] == sorted(row[0] for row in rows[1:])
    by_name = {row[0]: row for row in rows[1:]}
    assert by_name["SwcA"] == [
        "SwcA",
        "application",
        "davinci",
        "",
        "6",
        "2",
        "/ComponentTypes/SwcA",
        "Config/Developer/ComponentTypes/SwcA.arxml",
    ]
    assert by_name["NvM"][1:3] == ["service", "locked"]
    assert len(rows) == 1 + 12


def test_list_ports(repo_root: Path, tmp_path: Path) -> None:
    arguments = repo_arguments(repo_root, tmp_path)
    result = run(["list", "ports", *arguments, "--swc", "SwcB", "--format", "tsv"])
    assert result.exit_code == 0
    assert result.output.splitlines() == [
        "\t".join(report.PORT_HEADERS),
        "SwcB\tPpDiag\tP\t/PortInterfaces/IfDiag\tclient-server\t/ComponentTypes/SwcB/PpDiag",
        "SwcB\tRpEvents\tR\t/PortInterfaces/IfEvents\tsender-receiver\t/ComponentTypes/SwcB/RpEvents",
        "SwcB\tRpSpeed\tR\t/PortInterfaces/IfSpeed\tsender-receiver\t/ComponentTypes/SwcB/RpSpeed",
    ]
    everything = run(["list", "ports", *arguments, "--format", "tsv"])
    assert len(everything.output.splitlines()) == 1 + 14
    by_path = run(["list", "ports", *arguments, "--swc", "/ComponentTypes/SwcM"])
    assert "PpTorque" in by_path.output and "PpSpeed" not in by_path.output


def test_list_connectors(repo_root: Path, tmp_path: Path) -> None:
    result = run(["list", "connectors", *repo_arguments(repo_root, tmp_path), "--format", "tsv"])
    assert result.exit_code == 0
    lines = result.output.splitlines()
    assert lines[0] == "\t".join(report.CONNECTOR_HEADERS)
    assert lines[1:] == [
        "Cluster\tRpSpeedIn_SwcB1_RpSpeed\tdelegation\t(composition).RpSpeedIn\tSwcB1.RpSpeed",
        "Cluster\tSwcB2_PpDiag_PpDiagOut\tdelegation\tSwcB2.PpDiag\t(composition).PpDiagOut",
        "EcuComposition\tSwcA_PpEvents_SwcB_RpEvents\tassembly\tSwcA.PpEvents\tSwcB.RpEvents",
        "EcuComposition\tSwcA_PpSpeed_Cluster_RpSpeedIn\tassembly\tSwcA.PpSpeed\tCluster.RpSpeedIn",
        "EcuComposition\tSwcA_PpSpeed_SwcB_RpSpeed\tassembly\tSwcA.PpSpeed\tSwcB.RpSpeed",
        "EcuComposition\tSwcB_PpDiag_SwcA_RpDiag\tassembly\tSwcB.PpDiag\tSwcA.RpDiag",
    ]


def test_list_unconnected(repo_root: Path, tmp_path: Path) -> None:
    result = run(["list", "unconnected", *repo_arguments(repo_root, tmp_path)])
    assert result.exit_code == 0
    lines = result.output.splitlines()
    assert lines[0].split() == ["Composition", "Prototype", "Port", "Direction", "Interface"]
    assert set(lines[1]) == {"-", " "}
    assert len(lines) == 2 + 9
    assert lines[2].split() == ["Cluster", "SwcB1", "PpDiag", "P", "/PortInterfaces/IfDiag"]
    assert lines[-1].split() == [
        "EcuComposition",
        "SwcM",
        "PpTorque",
        "P",
        "/PortInterfaces/IfTorque",
    ]


def test_list_exits_with_1_when_the_model_has_error_findings(
    repo_root: Path, tmp_path: Path
) -> None:
    (repo_root / "Config" / "Developer" / "Broken.arxml").write_text("<AUTOSAR")
    result = run(["list", "swc", *repo_arguments(repo_root, tmp_path)])
    assert result.exit_code == 1 and "SwcA" in result.output


# ---- report helpers and the standalone runner


def test_load_blocking_reports_progress(repo_root: Path, tmp_path: Path) -> None:
    config = ProjectConfig(
        repo_root=repo_root,
        arxml_roots=(repo_root / "Config" / "Developer",),
        cache_dir=tmp_path / "cache",
    )
    seen: list[tuple[int, int, str]] = []
    model, stats = load_blocking(config, workers=2, on_progress=lambda *event: seen.append(event))
    assert len(model.components) == 12 and stats.files_total == 7
    assert seen[0] == (0, 0, "Scanning folders") and seen[-1] == (8, 8, "Model ready")


def test_report_rows_on_the_shared_sample_model() -> None:
    model: Model = build_sample_model()
    assert [row[0] for row in report.swc_rows(model)] == ["EcuComposition", "SwcA", "SwcB", "SwcM"]
    assert report.swc_rows(model)[1][:6] == ("SwcA", "application", "davinci", "Alpha", "3", "1")
    assert [row[1] for row in report.port_rows(model, "SwcA")] == ["PpSpeed", "RpDiag", "RpTorque"]
    assert report.connector_rows(model) == [
        ("EcuComposition", "SwcA_PpSpeed_SwcB_RpSpeed", "assembly", "SwcA.PpSpeed", "SwcB.RpSpeed"),
        ("EcuComposition", "SwcB_PpDiag_SwcA_RpDiag", "assembly", "SwcB.PpDiag", "SwcA.RpDiag"),
    ]
    assert report.unconnected_rows(model) == [
        ("EcuComposition", "SwcA", "RpTorque", "R", "/PortInterfaces/IfTorque"),
        ("EcuComposition", "SwcM", "PpTorque", "P", "/PortInterfaces/IfTorque"),
    ]
    assert report.has_errors(model) is False
    assert report.summary_lines(model)[0] == "Components: 4 (1 compositions)"


def test_format_table_and_tsv() -> None:
    rows = [("a", "long value"), ("bbb", "x\ty\nz")]
    assert report.format_table(("One", "Two"), rows) == (
        "One  Two\n---  ----------\na    long value\nbbb  x\ty\nz"
    )
    assert report.format_tsv(("One", "Two"), rows) == "One\tTwo\na\tlong value\nbbb\tx y z"
    assert report.format_table(("One",), []) == "One\n---"


def test_finding_lines_are_ranked_and_limited() -> None:
    from porttool.core.results import Finding, Severity

    findings = [
        Finding("RDR-A", Severity.INFO, "note", file="a.arxml"),
        Finding("RDR-B", Severity.ERROR, "bad", object_path="/P/X"),
        Finding("RDR-C", Severity.WARNING, "hm"),
    ]
    assert report.finding_lines(findings) == [
        "ERROR   RDR-B /P/X: bad",
        "WARNING RDR-C -: hm",
        "INFO    RDR-A a.arxml: note",
    ]
    assert report.finding_lines(findings, 1) == [
        "ERROR   RDR-B /P/X: bad",
        "... and 2 more findings",
    ]
    assert report.count_by_severity(findings) == {
        Severity.INFO: 1,
        Severity.WARNING: 1,
        Severity.ERROR: 1,
    }
