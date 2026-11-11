"""`discover run` end to end on the synthetic repo, including the named corner cases."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from porttool.core.errors import ConfigError, WriteError
from porttool.discovery import arxml_scan, fsio, register_cli, runner, sections
from porttool.discovery.runner import DiscoveryOptions, run_discovery
from tests.unit.discovery import synth
from tests.unit.discovery.conftest import Runner, codes, load_json


def _cli() -> click.Group:
    group = click.Group("porttool")
    register_cli(group)
    return group


# ---- the normal case


def test_report_folder_holds_json_markdown_readme_and_manifest(
    git_repo: Path, discover: Runner
) -> None:
    result = discover(git_repo)
    out = Path(result.out_dir)
    assert out == git_repo / "porttool_discovery"
    for name in ("discovery.json", "discovery.md", "README.txt", "manifest.json"):
        assert (out / name).is_file()
    report = load_json(out)
    assert report["format_version"] == 1
    assert [step["ok"] for step in report["steps"]] == [True] * 10
    assert report["mode"]["anonymized"] is False
    readme = (out / "README.txt").read_text(encoding="utf-8")
    assert "NOT anonymized" in readme and "discovery.json" in readme
    assert "Mode: NOT anonymized" in (out / "discovery.md").read_text(encoding="utf-8")


def test_inventory_counts_kinds_and_schema_version(git_repo: Path, discover: Runner) -> None:
    inventory = discover(git_repo).report["inventory"]
    assert inventory["by_type"]["arxml"]["count"] == 12
    assert inventory["by_type"]["ecuc.arxml"]["count"] == 1
    assert inventory["by_type"]["dcf"]["count"] == 1
    assert inventory["by_type"]["dpa"]["count"] == 1
    kinds = inventory["top_level_kinds"]
    assert kinds["APPLICATION-SW-COMPONENT-TYPE"]["objects"] == 3
    assert kinds["COMPOSITION-SW-COMPONENT-TYPE"]["objects"] == 2
    assert kinds["SENDER-RECEIVER-INTERFACE"]["objects"] == 2
    assert kinds["ECUC-MODULE-CONFIGURATION-VALUES"]["objects"] == 3
    assert inventory["internal_behaviors"] == 3
    assert inventory["schema_versions"][0]["version_hint"] == "4-2-2"
    assert inventory["largest_files"][0]["file"] == "Config/EcucCfg/EcuProj.ecuc.arxml"


def test_storage_map_tells_layout_folder_package_and_file_naming(
    git_repo: Path, discover: Runner
) -> None:
    kinds = discover(git_repo).report["storage_map"]["kinds"]
    swc = kinds["APPLICATION-SW-COMPONENT-TYPE"]
    assert swc["layout"] == "one-object-per-file"
    assert swc["folders"][0] == {
        "folder": "Config/DevWs/ComponentTypes",
        "files": 3,
        "objects": 3,
    }
    assert swc["packages"][0] == {"package": "/ComponentTypes", "objects": 3}
    assert swc["file_naming"][0]["pattern"] == "{name}"
    assert "PORTS" in swc["child_elements"]
    assert kinds["IMPLEMENTATION-DATA-TYPE"]["folders"][0]["folder"] == "Config/DevWs/DataTypes"
    assert kinds["SERVICE-SW-COMPONENT-TYPE"]["file_naming"][0]["pattern"] == "unrelated"
    assert kinds["ECUC-MODULE-CONFIGURATION-VALUES"]["layout"] == "many-objects-per-file"


def test_workspace_index_summarises_unknown_format_and_compares_with_disk(
    git_repo: Path, discover: Runner
) -> None:
    result = discover(git_repo)
    dcf = result.report["workspace"]["dcf"]["files"][0]
    root = dcf["structure"][0]
    assert root["tag"] == "DCF" and root["attributes"]["ARXMLVERSION"]["samples"] == ["4.2.2"]
    fileref = next(child for child in root["children"] if child["tag"] == "FILEREF")
    arxml = fileref["children"][0]
    assert arxml["tag"] == "ARXML" and arxml["count"] == 9
    assert len(arxml["text"]["samples"]) == 3
    assert arxml["attributes"]["ROOTITEM"]["distinct"] == "3"
    listing = dcf["arxml"]
    assert listing["listed"] == 9 and listing["listed_existing"] == 8
    assert listing["listed_missing"]["items"] == ["Config\\DevWs\\ComponentTypes\\GoneAway.arxml"]
    assert listing["on_disk_not_listed_in_folders_with_listed_files"]["items"] == [
        "Config/DevWs/ComponentTypes/Unlisted.arxml"
    ]
    assert dcf["paths"]["separators"] == {"backslash": 9}
    assert dcf["paths"]["relative_resolved_against_the_files_folder"]["existing"] == 8
    dpa = result.report["workspace"]["dpa"]["files"][0]
    locations = {row["location"]: row for row in dpa["paths"]["by_location"]}
    assert locations["/Project/Folders/EcucFolder#text"]["existing_folders"] == 1
    assert locations["/Project/DeveloperWorkspace#text"]["existing_files"] == 1
    assert "DISC-WS-MISSING" in codes(result)


def test_xml_style_reports_uuid_formats_and_admin_data_gids(
    git_repo: Path, discover: Runner
) -> None:
    style = discover(git_repo).report["xml_style"]
    overall = style["overall"]
    assert overall["line_endings"] == {"lf": 12}
    assert overall["indent"] == {"2 spaces": 12}
    assert overall["declaration"] == {'<?xml version="1.0" encoding="UTF-8"?>': 12}
    assert overall["uuid_format"]["8-4-4-4-12 lower"] >= 5
    assert overall["uuid_format"]["{}8-4-4-4-12 upper"] == 1
    assert overall["uuid_version_digit"]["4"] >= 5
    assert overall["admin_data"]["sdg_gids"] == {"DV:DEV": 1}
    assert overall["admin_data"]["sd_gids"] == {"DV:CREATED": 1}
    assert overall["admin_data"]["parent_elements"] == {"APPLICATION-SW-COMPONENT-TYPE": 1}
    assert overall["empty_elements"]["self_closing"] == 1
    assert overall["files_with_head_comment"] == 1
    folders = {row["folder"]: row for row in style["by_folder"]}
    assert folders["Config/DevWs/ComponentTypes"]["files"] == 6


def test_ecuc_lists_definition_suffixes_and_counts_mappings_and_tasks(
    git_repo: Path, discover: Runner
) -> None:
    ecuc = discover(git_repo).report["ecuc"]
    assert ecuc["modules_by_name"] == {"EcuC": 1, "Os": 1, "Rte": 1}
    rte = ecuc["rte"][0]
    assert rte["definition"] == "/MICROSAR/Rte"
    rows = {(r["element"], r["definition_suffix"]): r for r in rte["definitions"]}
    key = ("ECUC-CONTAINER-VALUE", "RteSwComponentInstance/RteEventToTaskMapping")
    assert rows[key]["count"] == 3
    assert rows[key]["definition_kind"] == "ECUC-PARAM-CONF-CONTAINER-DEF"
    position = (
        "ECUC-NUMERICAL-PARAM-VALUE",
        "RteSwComponentInstance/RteEventToTaskMapping/RtePositionInTask",
    )
    assert rows[position]["count"] == 3
    targets = {(r["definition_suffix"], r["value_ref_dest"]): r["count"]
               for r in rte["reference_targets"]}  # fmt: skip
    base = "RteSwComponentInstance/RteEventToTaskMapping/"
    assert targets[(base + "RteEventRef", "TIMING-EVENT")] == 3
    assert targets[(base + "RteMappedToTaskRef", "ECUC-CONTAINER-VALUE")] == 2
    assert rte["event_to_task_mapping"] == {
        "mapping_containers": 3,
        "with_task_reference": 2,
        "without_task_reference": 1,
        "with_event_reference": 3,
        "distinct_tasks_referenced": 1,
        "position_in_task_parameters": 3,
    }
    assert ecuc["os"][0]["tasks"] == 2
    assert ecuc["summary"]["events_mapped_to_a_task"] == 2
    text = str(ecuc)
    assert "OsTask_10ms" not in text and "Map_TMT" not in text  # structure only


def test_composition_shape_finds_nesting_connectors_and_root(
    git_repo: Path, discover: Runner
) -> None:
    composition = discover(git_repo).report["composition"]
    assert composition["compositions"] == 2
    assert composition["nested"] is True and composition["max_nesting_depth"] == 2
    assert composition["assembly_connectors"] == 2
    assert composition["delegation_connectors"] == 1
    assert composition["connector_files"] == [
        {
            "file": "Config/DevWs/ComponentTypes/EcuComposition.arxml",
            "assembly": 2,
            "delegation": 1,
        }
    ]
    root = composition["root_candidates"]
    assert [item["path"] for item in root] == ["/ComponentTypes/EcuComposition"]
    assert "SOFTWARE-COMPOSITION-TREF" in root[0]["reason"]


def test_owner_proposal_marks_model_and_locked_candidates(git_repo: Path, discover: Runner) -> None:
    result = discover(git_repo)
    owners = result.report["owners"]
    model = owners["model_candidates"]
    assert [item["swc"] for item in model] == ["SwcModel"]
    assert model[0]["authoring_tool"] == "Simulink"
    assert {"keyword": "Embedded Coder", "where": "comment", "count": 1} in model[0]["evidence"]
    locked = owners["locked_candidates"]
    assert [(item["swc"], item["reason"]) for item in locked] == [
        ("DetService", "service component type")
    ]
    csv_text = (Path(result.out_dir) / "owners_proposed.csv").read_text(encoding="utf-8")
    lines = csv_text.splitlines()
    assert lines[0] == "swc,team,arxml_owner,authoring_tool,export_folder"
    assert "SwcModel,,model,Simulink,Config/DevWs/ComponentTypes" in lines
    assert "SwcAlpha,,davinci,," in lines
    assert not any(line.startswith("DetService") for line in lines)
    locked_text = (Path(result.out_dir) / "locked_proposed.txt").read_text(encoding="utf-8")
    assert locked_text == "DetService\n"


def test_generated_folder_component_is_a_locked_candidate(repo: Path, discover: Runner) -> None:
    synth.write(repo, "Appl/GenData/Gen_swc.arxml", synth.package("Gen", synth.swc("GenSwc", "")))
    locked = discover(repo).report["owners"]["locked_candidates"]
    assert ("GenSwc", "folder name matches gendata*") in [
        (item["swc"], item["reason"]) for item in locked
    ]


def test_lock_and_temp_files_are_listed_with_timestamps(git_repo: Path, discover: Runner) -> None:
    synth.write(git_repo, "Config/DevWs/ComponentTypes/~SwcAlpha.tmp", "x")
    result = discover(git_repo)
    locks = result.report["lock_signs"]
    assert locks["count"] == 3
    assert locks["by_pattern"] == {"*.bak": 1, "*.lock": 1, "~*": 1}
    first = locks["items"][0]
    assert first["in_workspace_folder"] is True and first["modified_utc"].endswith("Z")
    assert "DISC-LOCK-FOUND" in codes(result)


# ---- corner cases: workspace files


def test_repo_without_dcf_or_dpa_still_gives_a_report(repo: Path, discover: Runner) -> None:
    (repo / "EcuProj.dcf").unlink()
    (repo / "Config" / "EcuProj.dpa").unlink()
    result = discover(repo)
    assert {"DISC-NO-DCF", "DISC-NO-DPA"} <= set(codes(result))
    assert result.report["workspace"]["dcf"] == {"found": 0, "not_analysed": [], "files": []}
    assert result.report["inventory"]["by_type"]["arxml"]["count"] == 12
    assert all(step["ok"] for step in result.report["steps"])


def test_several_dcf_files_are_each_summarised(repo: Path, discover: Runner) -> None:
    synth.write(repo, "Config/Other.dcf", synth.dcf_text(synth.DCF_FILES[:2]))
    result = discover(repo)
    block = result.report["workspace"]["dcf"]
    assert block["found"] == 2
    assert [item["file"] for item in block["files"]] == ["EcuProj.dcf", "Config/Other.dcf"]
    assert "DISC-MANY-DCF" in codes(result)
    # the second file's paths are relative to its own folder, so they do not resolve there
    other = block["files"][1]["paths"]
    assert other["relative_resolved_against_the_files_folder"]["existing"] == 0
    assert other["relative_resolved_against_the_repo_root"]["existing"] == 2


def test_malformed_dcf_gives_partial_structure_text_summary_and_finding(
    repo: Path, discover: Runner
) -> None:
    broken = synth.dcf_text().replace("  </FILEREF>\n</DCF>\n", "  </FILEREF>\n</WRONG>\n")
    synth.write(repo, "EcuProj.dcf", broken)
    result = discover(repo)
    dcf = result.report["workspace"]["dcf"]["files"][0]
    assert dcf["xml_ok"] is False
    assert dcf["problem"]["message"] == "mismatched tag" and dcf["problem"]["line"] == 16
    assert dcf["structure"][0]["tag"] == "DCF"  # the part before the error
    assert dcf["text_fallback"]["lines"] == 16
    assert dcf["arxml"]["listed"] == 9
    assert "DISC-WS-MALFORMED" in codes(result)
    assert all(step["ok"] for step in result.report["steps"])


def test_dcf_that_is_not_xml_at_all_is_described_by_line_shapes(
    repo: Path, discover: Runner
) -> None:
    synth.write(
        repo,
        "EcuProj.dcf",
        "[Files]\nFile1=Config\\DevWs\\DataTypes\\DataTypes.arxml\nFile2=Nope\\Gone.arxml\n",
    )
    dcf = discover(repo).report["workspace"]["dcf"]["files"][0]
    assert dcf["xml_ok"] is False and dcf["structure"] == []
    shapes = {row["shape"]: row["lines"] for row in dcf["text_fallback"]["line_shapes"]}
    assert shapes["w=w\\w\\w\\w.w"] == 1
    assert dcf["arxml"]["listed"] == 2 and dcf["arxml"]["listed_missing"]["count"] == 1


def test_explicit_dpa_outside_the_walk_is_used(
    repo: Path, tmp_path: Path, discover: Runner
) -> None:
    (repo / "Config" / "EcuProj.dpa").unlink()
    elsewhere = synth.write(tmp_path / "elsewhere", "Other.dpa", synth.DPA)
    result = discover(repo, dpa=str(elsewhere))
    dpa = result.report["workspace"]["dpa"]
    assert dpa["found"] == 1 and dpa["files"][0]["file"] == "Other.dpa"
    assert "DISC-NO-DPA" not in codes(result)
    missing = discover(repo, dpa=str(tmp_path / "nope.dpa"))
    assert "DISC-DPA-OPTION" in codes(missing)


# ---- corner cases: ARXML content


def test_arxml_with_bom_utf16_and_crlf_is_read_and_style_is_reported(
    repo: Path, discover: Runner
) -> None:
    text = synth.SWC_BETA.replace("SwcBeta", "SwcBom")
    synth.write(repo, "Enc/Bom.arxml", b"\xef\xbb\xbf" + text.encode("utf-8"))
    utf16 = text.replace("SwcBom", "SwcWide").replace('encoding="UTF-8"', 'encoding="UTF-16"')
    synth.write(repo, "Enc/Wide.arxml", utf16.encode("utf-16"))
    crlf = text.replace("SwcBom", "SwcCrlf").replace("\n", "\r\n").replace("  ", "\t")
    synth.write(repo, "Enc/Crlf.arxml", crlf.encode("utf-8"))
    result = discover(repo)
    assert result.report["inventory"]["problem_files"] == []
    assert (
        result.report["inventory"]["top_level_kinds"]["APPLICATION-SW-COMPONENT-TYPE"]["objects"]
        == 6
    )
    folder = next(row for row in result.report["xml_style"]["by_folder"] if row["folder"] == "Enc")
    assert folder["bom"] == {"none": 1, "utf-16-le": 1, "utf-8": 1}
    assert folder["declared_encoding"] == {"UTF-8": 2, "UTF-16": 1}
    assert folder["line_endings"] == {"lf": 2, "crlf": 1}
    assert folder["indent"] == {"2 spaces": 2, "1 tab": 1}
    assert folder["trailing_newline"] == {"lf": 2, "crlf": 1}
    names = [
        item["short_name"]
        for item in result.report["storage_map"]["kinds"]["APPLICATION-SW-COMPONENT-TYPE"][
            "file_naming"
        ][0]["samples"]
    ]
    assert len(names) == 3


def test_empty_and_truncated_files_are_listed_never_fatal(repo: Path, discover: Runner) -> None:
    synth.write(repo, "Bad/Empty.arxml", b"")
    synth.write(repo, "Bad/Truncated.arxml", synth.SWC_ALPHA[:900])
    synth.write(repo, "Bad/Garbage.arxml", b"\x00\x01\x02 not xml")
    result = discover(repo)
    problems = {row["file"]: row for row in result.report["inventory"]["problem_files"]}
    assert problems["Bad/Empty.arxml"]["problem"] == "empty"
    assert problems["Bad/Truncated.arxml"]["problem"] == "malformed"
    assert problems["Bad/Truncated.arxml"]["line"] is not None
    assert problems["Bad/Garbage.arxml"]["problem"] in ("malformed", "encoding")
    assert {"DISC-XML-EMPTY", "DISC-XML-MALFORMED"} <= set(codes(result))
    assert all(step["ok"] for step in result.report["steps"])
    # the truncated file still contributes what was read before the cut
    per_file = {row["file"]: row for row in result.report["inventory"]["arxml_files"]}
    assert per_file["Bad/Truncated.arxml"]["elements"] > 5


def test_unreadable_file_is_a_finding(
    repo: Path, discover: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    def guarded(path: str):  # type: ignore[no-untyped-def]
        if path.endswith("SwcBeta.arxml"):
            raise PermissionError("locked by another process")
        return fsio.read_chunks(path)

    monkeypatch.setattr("porttool.discovery.runner.read_chunks", guarded)
    result = discover(repo)
    problems = result.report["inventory"]["problem_files"]
    assert [(row["problem"], row["message"]) for row in problems] == [
        ("unreadable", "PermissionError")
    ]
    assert "DISC-FILE-UNREADABLE" in codes(result)
    assert result.report["inventory"]["by_type"]["arxml"]["count"] == 12


def test_a_file_that_makes_the_scanner_fail_does_not_stop_the_inventory(
    repo: Path, discover: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fragile(rel: str, size: int, chunks):  # type: ignore[no-untyped-def]
        if rel.endswith("SwcBeta.arxml"):
            raise ValueError("a bug in the scanner")
        return arxml_scan.scan_arxml(rel, size, chunks)

    monkeypatch.setattr("porttool.discovery.runner.scan_arxml", fragile)
    result = discover(repo)
    assert "DISC-FILE-FAILED" in codes(result)
    assert all(step["ok"] for step in result.report["steps"])
    kinds = result.report["inventory"]["top_level_kinds"]
    assert kinds["APPLICATION-SW-COMPONENT-TYPE"]["objects"] == 2


def test_non_ascii_and_very_long_paths_work(repo: Path, discover: Runner) -> None:
    deep = "/".join(["Verzeichnis_mit_Umlaut_äöü_und_Leerzeichen " + str(i) for i in range(8)])
    rel = f"Zusätzlich/{deep}/Größe Ω.arxml"
    path = synth.write(repo, rel, synth.SWC_BETA.replace("SwcBeta", "SwcLong"))
    assert len(str(path)) > 300
    result = discover(repo)
    files = [row["file"] for row in result.report["inventory"]["arxml_files"]]
    assert rel in files
    assert result.report["inventory"]["problem_files"] == []
    assert "Größe Ω.arxml" in (Path(result.out_dir) / "discovery.md").read_text(encoding="utf-8")


def test_exclude_globs_skip_folders_and_git_folder_is_never_read(
    git_repo: Path, discover: Runner
) -> None:
    synth.write(git_repo, ".git/hidden.arxml", synth.SWC_BETA)
    result = discover(git_repo, exclude=("Config/EcucCfg/**", "*.bak", "System"))
    inventory = result.report["inventory"]
    assert inventory["by_type"]["arxml"]["count"] == 10
    assert result.report["ecuc"]["module_configurations"] == 0
    assert result.report["lock_signs"]["by_pattern"] == {"*.lock": 1}
    assert {"DISC-ECUC-NO-RTE", "DISC-ECUC-NO-OS"} <= set(codes(result))


def test_second_run_reuses_its_folder_and_never_scans_it(git_repo: Path, discover: Runner) -> None:
    first = discover(git_repo)
    synth.write(Path(first.out_dir), "stray.arxml", synth.SWC_BETA)
    second = discover(git_repo)
    assert second.report["inventory"]["by_type"]["arxml"]["count"] == 12


# ---- corner cases: git


def test_no_git_executable_is_a_finding_not_a_failure(repo: Path, discover: Runner) -> None:
    result = discover(repo, git_executable="git-that-does-not-exist-anywhere")
    assert result.report["git"] == {"available": False, "is_repo": None}
    assert "DISC-GIT-MISSING" in codes(result)
    assert all(step["ok"] for step in result.report["steps"])


def test_folder_that_is_not_a_git_repo_is_reported(repo: Path, discover: Runner) -> None:
    result = discover(repo)
    assert result.report["git"]["available"] is True
    assert result.report["git"]["is_repo"] is False
    assert "DISC-GIT-NOT-REPO" in codes(result)


def test_clean_git_tree_reports_branch_and_ignored_generated_folder(
    git_repo: Path, discover: Runner
) -> None:
    git = discover(git_repo).report["git"]
    assert git["is_repo"] is True and git["branch"] == "main"
    assert git["clean"] is True
    assert git["changes"] == {"staged": 0, "not_staged": 0, "untracked": 0}
    assert len(git["head_commit"]) == 40
    assert git["generated_folders"] == [
        {"folder": "Appl/GenData", "ignored": True, "tracked_files": 0}
    ]
    assert git["gitignore_at_root"] is True
    assert git["attributes_of_one_sample_file_per_type"]["arxml"]["text"] == "unspecified"
    assert git["repo_folder_is_work_tree_root"] is True


def test_dirty_git_tree_is_counted_without_our_own_output_folder(
    git_repo: Path, discover: Runner
) -> None:
    discover(git_repo)  # leaves porttool_discovery behind: must not count as dirty
    (git_repo / "EcuProj.dcf").write_text("changed", encoding="utf-8")
    synth.write(git_repo, "New/Untracked.arxml", synth.SWC_BETA)
    synth.write(git_repo, "Staged.txt", "x")
    synth.git(git_repo, "add", "Staged.txt")
    result = discover(git_repo)
    git = result.report["git"]
    assert git["clean"] is False
    assert git["changes"] == {"staged": 1, "not_staged": 1, "untracked": 1}
    assert "DISC-GIT-DIRTY" in codes(result)
    listing = str(result.report["git"])
    assert "Untracked" not in listing and "Staged" not in listing  # counts only


def test_discovery_does_not_change_the_repo(git_repo: Path, discover: Runner) -> None:
    discover(git_repo)
    status = synth.git(git_repo, "status", "--porcelain")
    assert status == "?? porttool_discovery/"


# ---- robustness


def test_a_failing_step_becomes_a_finding_and_the_other_steps_still_run(
    git_repo: Path, discover: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise RuntimeError("secret detail of the failure")

    monkeypatch.setattr(runner, "build_storage_map", explode)
    monkeypatch.setattr(runner, "build_ecuc", explode)
    result = discover(git_repo)
    by_step = {step["step"]: step["ok"] for step in result.report["steps"]}
    assert by_step["storage_map"] is False and by_step["ecuc"] is False
    assert sum(by_step.values()) == 8
    assert codes(result).count("DISC-STEP-FAILED") == 2
    assert result.report["storage_map"] == {} and result.report["composition"]["compositions"] == 2
    errors = result.report["step_errors"]
    assert errors[0]["error"] == "RuntimeError" and errors[0]["frames"]
    assert "Steps that FAILED: Storage map; ECUC structure (Rte, Os)" in result.summary
    assert (Path(result.out_dir) / "discovery.md").is_file()


def test_progress_is_printed_per_step_and_summary_fits_one_screen(git_repo: Path) -> None:
    lines: list[str] = []
    result = run_discovery(DiscoveryOptions(repo=str(git_repo), no_tools=True), lines.append)
    starts = [line for line in lines if line.startswith("[")]
    assert len(starts) == 10 and starts[0].startswith("[1/10]") and starts[-1].startswith("[10/10]")
    assert len(result.summary.splitlines()) <= 24
    assert result.out_dir in result.summary


def test_output_folder_not_writable_fails_before_any_work(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocker = tmp_path / "a_file"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(WriteError, match="not a folder"):
        run_discovery(DiscoveryOptions(repo=str(repo), out=str(blocker), no_tools=True))
    with pytest.raises(WriteError, match="cannot be written"):
        run_discovery(DiscoveryOptions(repo=str(repo), out=str(blocker / "sub"), no_tools=True))

    def denied(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("access denied")

    monkeypatch.setattr(runner, "atomic_write_bytes", denied)
    monkeypatch.setattr(runner, "walk_repo", lambda *_a: pytest.fail("work started"))
    with pytest.raises(WriteError, match="cannot be written"):
        run_discovery(DiscoveryOptions(repo=str(repo), no_tools=True))


def test_output_folder_with_foreign_files_is_refused(repo: Path, tmp_path: Path) -> None:
    build = tmp_path / "build_output"
    build.mkdir()
    (build / "Rte.c").write_text("generated", encoding="utf-8")
    with pytest.raises(WriteError, match="did not write"):
        run_discovery(DiscoveryOptions(repo=str(repo), out=str(build), no_tools=True))
    assert sorted(os.listdir(build)) == ["Rte.c"]


def test_one_report_file_that_cannot_be_written_is_a_finding(
    repo: Path, discover: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    def picky(path: str, text: str, *, newline: str = "\n") -> None:
        if path.endswith("discovery.md"):
            raise PermissionError("open in an editor")
        fsio.atomic_write_text(path, text, newline=newline)

    monkeypatch.setattr("porttool.discovery.runner.atomic_write_text", picky)
    result = discover(repo)
    assert not (Path(result.out_dir) / "discovery.md").exists()
    report = load_json(result.out_dir)
    assert "DISC-OUT-WRITE" in [finding["code"] for finding in report["findings"]]


def test_missing_repo_folder_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        run_discovery(DiscoveryOptions(repo=str(tmp_path / "nowhere"), no_tools=True))


def test_no_tools_starts_nothing(
    git_repo: Path, discover: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "build_tools", lambda *_a, **_k: pytest.fail("tools searched"))
    result = discover(git_repo, no_tools=True)
    tools = result.report["tools"]
    assert tools["skipped"] is True and tools["executables"] == []
    assert result.report["mode"]["vector_tools_started"] is False
    assert not (Path(result.out_dir) / "cli").exists()
    assert "DISC-TOOLS-SKIPPED" in codes(result)
    assert "Tools: skipped (--no-tools)" in result.summary


# ---- command line


def test_cli_run_exits_0_with_findings_and_prints_the_folder(git_repo: Path) -> None:
    shutil.rmtree(git_repo / "Config" / "EcucCfg")
    done = CliRunner().invoke(_cli(), ["discover", "run", "--repo", str(git_repo), "--no-tools"])
    assert done.exit_code == 0, done.output
    assert "[10/10]" in done.output
    assert f"Report folder: {git_repo / 'porttool_discovery'}" in done.output
    assert "warnings" in done.output


def test_cli_run_exits_2_with_a_clear_message_when_the_report_cannot_be_written(
    repo: Path, tmp_path: Path
) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    done = CliRunner().invoke(
        _cli(), ["discover", "run", "--repo", str(repo), "--out", str(blocker), "--no-tools"]
    )
    assert done.exit_code == 2
    assert "[PT-E400]" in done.output and "Traceback" not in done.output
    gone = CliRunner().invoke(_cli(), ["discover", "run", "--repo", str(tmp_path / "x")])
    assert gone.exit_code == 2 and "[PT-E100]" in gone.output


def test_cli_unexpected_error_writes_a_log_and_shows_no_traceback(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from porttool.discovery import cli

    def crash(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(cli, "run_discovery", crash)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    done = CliRunner().invoke(_cli(), ["discover", "run", "--repo", str(repo)])
    assert done.exit_code == 2
    assert "Traceback" not in done.output and "RuntimeError" in done.output
    logs = list((tmp_path / "appdata" / "PortTool" / "logs").glob("discover_crash_*.log"))
    assert len(logs) == 1 and "boom" in logs[0].read_text(encoding="utf-8")


def test_cli_passes_every_option(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from porttool.discovery import cli

    seen: list[DiscoveryOptions] = []

    def record(options: DiscoveryOptions, _progress: object) -> object:
        seen.append(options)
        return runner.DiscoveryResult(out_dir="o", private_dir=None, report={}, summary="ok")

    monkeypatch.setattr(cli, "run_discovery", record)
    done = CliRunner().invoke(
        _cli(),
        ["discover", "run", "--repo", str(repo), "--dpa", "p.dpa", "--sip", "S", "--developer-bin",
         "B", "--out", "O", "--anonymize", "--no-tools", "--exclude", "a", "--exclude", "b",
         "--git", "G"],
    )  # fmt: skip
    assert done.exit_code == 0, done.output
    options = seen[0]
    assert (options.dpa, options.sip, options.developer_bin, options.out) == (
        "p.dpa",
        "S",
        "B",
        "O",
    )
    assert options.anonymize and options.no_tools and options.exclude == ("a", "b")
    assert options.git_executable == "G"


def test_locked_folder_patterns_match_whole_segments_only() -> None:
    assert sections.locked_folder_reason("Appl/GenDataVtt/x") == "folder name matches gendata*"
    assert sections.locked_folder_reason("Config/ServiceComponents") is not None
    assert sections.locked_folder_reason("Config/MyGenDataLike") is None
