"""`discover golden-diff`: element-level and byte-level comparison of two commits."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click
import pytest
from click.testing import CliRunner

from porttool.core.errors import GitError
from porttool.discovery import register_cli
from porttool.discovery.golden import diff_xml, parse_tree
from porttool.discovery.names import Names
from porttool.discovery.runner import GoldenDiffOptions, run_golden_diff
from tests.unit.discovery import synth
from tests.unit.discovery.conftest import load_json

SWC_ALPHA = "Config/Developer/ComponentTypes/SwcAlpha.arxml"
NEW_PORT = synth.port("P", "PpNew", "IfSpeed", "99999999-aaaa-4bbb-8ccc-dddddddddddd")


def _reference_change(repo: Path) -> tuple[str, str]:
    """Commit a hand-made change of every kind the corner cases name; return (base, head)."""
    base = synth.git_init(repo)
    alpha = synth.SWC_ALPHA.replace("          </PORTS>\n", NEW_PORT + "          </PORTS>\n")
    synth.write(repo, SWC_ALPHA, alpha)
    synth.write(repo, "Config/Developer/PortInterfaces/IfNew.arxml", synth.interface("IfNew"))
    new_ref = ("PORTINTERFACE", "Config\\Developer\\PortInterfaces\\IfNew.arxml")
    synth.write(repo, "EcuProj.dcf", synth.dcf_text((*synth.DCF_FILES, new_ref)))
    composition = synth.ECU_COMPOSITION
    start = composition.index("            <DELEGATION-SW-CONNECTOR>")
    end = composition.index("</DELEGATION-SW-CONNECTOR>\n") + len("</DELEGATION-SW-CONNECTOR>\n")
    synth.write(
        repo,
        "Config/Developer/ComponentTypes/EcuComposition.arxml",
        composition[:start] + composition[end:],
    )
    synth.write(
        repo,
        "Config/Developer/ComponentTypes/SwcBeta.arxml",
        synth.SWC_BETA.replace("\n", "\r\n"),
    )
    synth.write(
        repo,
        "Config/Developer/DataTypes/DataTypes.arxml",
        synth.DATA_TYPES.replace("<CATEGORY>LINEAR</CATEGORY>", "<CATEGORY>TEXTTABLE</CATEGORY>"),
    )
    synth.write(
        repo,
        "Config/Developer/ComponentTypes/SubComp.arxml",
        synth.SUB_COMP.replace("  ", "    "),
    )
    (repo / "Config" / "System" / "FlatExtract.arxml").unlink()
    synth.write(repo, "Notes.txt", "not xml")
    return base, synth.commit_all(repo, "reference change")


@pytest.fixture
def golden(repo: Path) -> dict[str, Any]:
    base, head = _reference_change(repo)
    result = run_golden_diff(GoldenDiffOptions(repo=str(repo), base=base, head=head))
    report = result.report["golden_diff"]
    report["_files"] = {item["path"]: item for item in report["files"]}
    report["_result"] = result
    return report


def test_golden_diff_lists_changed_files_with_status(golden: dict[str, Any]) -> None:
    status = {path: item["status"] for path, item in golden["_files"].items()}
    assert status == {
        "Config/Developer/ComponentTypes/EcuComposition.arxml": "modified",
        "Config/Developer/ComponentTypes/SubComp.arxml": "modified",
        "Config/Developer/ComponentTypes/SwcAlpha.arxml": "modified",
        "Config/Developer/ComponentTypes/SwcBeta.arxml": "modified",
        "Config/Developer/DataTypes/DataTypes.arxml": "modified",
        "Config/Developer/PortInterfaces/IfNew.arxml": "added",
        "Config/System/FlatExtract.arxml": "deleted",
        "EcuProj.dcf": "modified",
        "Notes.txt": "added",
    }
    assert golden["_files"]["Notes.txt"]["xml"] is False
    summary = golden["summary"]
    assert (summary["added"], summary["deleted"], summary["modified"]) == (2, 1, 6)
    assert summary["xml_files_compared"] == 8
    assert len(golden["base_commit"]) == 40 and len(golden["head_commit"]) == 40


def test_golden_diff_added_port_gives_paths_position_and_exact_xml_text(
    golden: dict[str, Any],
) -> None:
    item = golden["_files"][SWC_ALPHA]
    assert item["summary"]["elements_added"] == 1
    assert item["summary"]["elements_removed"] == 0 and item["summary"]["elements_changed"] == 0
    change = item["elements"][0]
    assert change["change"] == "added"
    assert change["tag_path"] == (
        "/AUTOSAR/AR-PACKAGES/AR-PACKAGE/ELEMENTS/APPLICATION-SW-COMPONENT-TYPE/PORTS/"
        "P-PORT-PROTOTYPE"
    )
    assert change["short_name_path"] == "/ComponentTypes/SwcAlpha/PpNew"
    assert change["xml"] == NEW_PORT.rstrip("\n")  # the exact text, with its indentation
    assert change["xml_is_exact_source_text"] is True and change["indentation"] == " " * 10
    position = change["position"]
    assert position["index"] == 2 and position["of"] == 3
    assert position["after"] == {"tag": "R-PORT-PROTOTYPE", "short_name": "RpTorque"}
    assert position["before"] is None
    assert item["bytes"]["changed"] == []


def test_golden_diff_added_file_holds_the_whole_file_as_template(golden: dict[str, Any]) -> None:
    item = golden["_files"]["Config/Developer/PortInterfaces/IfNew.arxml"]
    assert item["status"] == "added" and item["bytes"]["base"] is None
    assert item["bytes"]["head"]["line_endings"] == "lf"
    change = item["elements"][0]
    assert change["change"] == "added" and change["tag_path"] == "/AUTOSAR"
    assert change["xml"].startswith("<AUTOSAR xmlns=") and change["xml"].endswith("</AUTOSAR>")
    assert "<SHORT-NAME>IfNew</SHORT-NAME>" in change["xml"]
    # the workspace index gained one entry: the template for registering a new file
    dcf = golden["_files"]["EcuProj.dcf"]["elements"]
    assert [c["change"] for c in dcf] == ["added"]
    assert dcf[0]["tag_path"] == "/DCF/FILEREF/ARXML"
    assert dcf[0]["xml"] == (
        '    <ARXML ROOTITEM="PORTINTERFACE" TYPE="">'
        "Config\\Developer\\PortInterfaces\\IfNew.arxml</ARXML>"
    )
    assert dcf[0]["position"]["index"] == 9


def test_golden_diff_deleted_element_and_deleted_file_are_listed_without_text(
    golden: dict[str, Any],
) -> None:
    item = golden["_files"]["Config/Developer/ComponentTypes/EcuComposition.arxml"]
    assert item["summary"]["elements_removed"] == 1
    change = item["elements"][0]
    assert change["change"] == "removed" and "xml" not in change
    assert change["tag_path"].endswith("/CONNECTORS/DELEGATION-SW-CONNECTOR")
    assert change["short_name_path"] == "/ComponentTypes/EcuComposition/Deleg_PpSpeed"
    deleted = golden["_files"]["Config/System/FlatExtract.arxml"]
    assert deleted["bytes"]["head"] is None
    assert [c["change"] for c in deleted["elements"]] == ["removed"]
    assert deleted["elements"][0]["tag_path"] == "/AUTOSAR"


def test_golden_diff_file_whose_only_change_is_line_endings(golden: dict[str, Any]) -> None:
    item = golden["_files"]["Config/Developer/ComponentTypes/SwcBeta.arxml"]
    assert item["elements"] == []
    assert item["summary"]["only_line_endings_changed"] is True
    assert item["summary"]["element_content_identical"] is True
    assert item["summary"]["only_layout_changed"] is False
    assert item["bytes"]["changed"] == ["line_endings", "trailing_newline"]
    assert item["bytes"]["base"]["line_endings"] == "lf"
    assert item["bytes"]["head"]["line_endings"] == "crlf"
    assert golden["summary"]["files_with_only_line_ending_changes"] == 1


def test_golden_diff_reindented_file_is_layout_only(golden: dict[str, Any]) -> None:
    item = golden["_files"]["Config/Developer/ComponentTypes/SubComp.arxml"]
    assert item["elements"] == [] and item["summary"]["only_layout_changed"] is True
    assert item["bytes"]["changed"] == ["indent"]
    assert (item["bytes"]["base"]["indent"], item["bytes"]["head"]["indent"]) == (
        "2 spaces",
        "4 spaces",
    )


def test_golden_diff_changed_value_is_reported_as_shape_not_as_value(
    golden: dict[str, Any],
) -> None:
    item = golden["_files"]["Config/Developer/DataTypes/DataTypes.arxml"]
    change = item["elements"][0]
    assert change["change"] == "changed"
    assert change["tag_path"].endswith("/COMPU-METHOD/CATEGORY")
    assert change["short_name_path"] == "/MyDataTypes/CmSpeed"
    assert change["what"] == {"text": {"old_shape": "AAAAAA", "new_shape": "AAAAAAAAA"}}
    assert "TEXTTABLE" not in str(item)


def test_golden_diff_writes_json_markdown_and_updates_readme(golden: dict[str, Any]) -> None:
    result = golden["_result"]
    out = Path(result.out_dir)
    stem = f"golden_diff_{golden['base_commit'][:8]}_{golden['head_commit'][:8]}"
    assert (out / f"{stem}.json").is_file() and (out / f"{stem}.md").is_file()
    markdown = (out / f"{stem}.md").read_text(encoding="utf-8")
    assert "### added: `/AUTOSAR/AR-PACKAGES/AR-PACKAGE/ELEMENTS" in markdown
    assert "<SHORT-NAME>PpNew</SHORT-NAME>" in markdown
    assert "only line endings changed" in markdown
    assert f"{stem}.json: NOT anonymized" in (out / "README.txt").read_text(encoding="utf-8")
    assert load_json(out, f"{stem}.json")["golden_diff"]["summary"]["elements_added"] == 3
    assert "Elements added: 3, removed: 2, changed: 1" in result.summary


def test_golden_diff_reads_committed_content_not_the_work_tree(repo: Path) -> None:
    base, head = _reference_change(repo)
    (repo / SWC_ALPHA).write_text("work tree garbage", encoding="utf-8")
    result = run_golden_diff(GoldenDiffOptions(repo=str(repo), base=base, head=head))
    files = {item["path"]: item for item in result.report["golden_diff"]["files"]}
    assert files[SWC_ALPHA]["summary"]["elements_added"] == 1
    assert result.report["findings"] == []


def test_golden_diff_malformed_version_is_a_finding(repo: Path) -> None:
    base = synth.git_init(repo)
    synth.write(repo, SWC_ALPHA, synth.SWC_ALPHA[:700])
    head = synth.commit_all(repo, "broken")
    result = run_golden_diff(GoldenDiffOptions(repo=str(repo), base=base, head=head))
    item = result.report["golden_diff"]["files"][0]
    assert "head" in item["problems"] and item["elements"] == []
    assert [f["code"] for f in result.report["findings"]] == ["DISC-GOLDEN-XML"]


def test_golden_diff_unknown_commit_no_repo_and_no_git_raise_clear_errors(
    repo: Path, tmp_path: Path
) -> None:
    with pytest.raises(GitError, match="not a git work tree"):
        run_golden_diff(GoldenDiffOptions(repo=str(repo), base="HEAD~1", head="HEAD"))
    synth.git_init(repo)
    with pytest.raises(GitError, match="does not know the commit 'nope'"):
        run_golden_diff(GoldenDiffOptions(repo=str(repo), base="nope", head="HEAD"))
    with pytest.raises(GitError, match="is not a commit"):
        run_golden_diff(GoldenDiffOptions(repo=str(repo), base="--output=x", head="HEAD"))
    with pytest.raises(GitError, match="could not be started"):
        run_golden_diff(
            GoldenDiffOptions(repo=str(repo), base="HEAD", head="HEAD", git_executable="no-git-x")
        )


def test_cli_golden_diff_exit_codes(repo: Path) -> None:
    base, head = _reference_change(repo)
    group = click.Group("porttool")
    register_cli(group)
    done = CliRunner().invoke(
        group, ["discover", "golden-diff", "--repo", str(repo), "--base", base, "--head", head]
    )
    assert done.exit_code == 0, done.output
    assert "Files changed: 9" in done.output and "Report folder:" in done.output
    bad = CliRunner().invoke(
        group, ["discover", "golden-diff", "--repo", str(repo), "--base", "zzz", "--head", head]
    )
    assert bad.exit_code == 2 and "[PT-E500]" in bad.output and "Traceback" not in bad.output


# ---- the comparison itself


def _diff(old: str, new: str, path: str = "x.arxml") -> dict[str, Any]:
    return diff_xml(path, old.encode(), new.encode(), Names())


def test_diff_detects_reordered_children_attribute_changes_and_renames() -> None:
    old = (
        '<R><P UUID="aa11"><SHORT-NAME>A</SHORT-NAME></P>'
        "<P><SHORT-NAME>B</SHORT-NAME></P><Q>1</Q></R>"
    )
    new = (
        '<R><P><SHORT-NAME>B</SHORT-NAME></P><P UUID="BB22"><SHORT-NAME>A</SHORT-NAME></P>'
        "<P><SHORT-NAME>C</SHORT-NAME></P></R>"
    )
    elements = _diff(old, new)["elements"]
    by_kind = {(e["change"], e["short_name_path"] or e["tag_path"]): e for e in elements}
    assert by_kind[("changed", "/R")]["what"] == {"children_reordered": True}
    assert by_kind[("changed", "/A")]["what"]["attributes"] == [
        {"name": "UUID", "old_shape": "aa99", "new_shape": "AA99"}
    ]
    assert ("added", "/C") in by_kind and ("removed", "/R/Q") in by_kind
    assert by_kind[("added", "/C")]["position"]["siblings_with_this_tag"] == "not sorted"


def test_diff_pairs_unnamed_children_by_content_first() -> None:
    old = "<L><REF>/a</REF><REF>/b</REF></L>"
    new = "<L><REF>/x</REF><REF>/a</REF><REF>/b</REF></L>"
    elements = _diff(old, new)["elements"]
    assert [(e["change"], e["xml"]) for e in elements] == [("added", "<REF>/x</REF>")]
    assert elements[0]["position"]["index"] == 0


def test_diff_keeps_comments_self_closing_tags_and_crlf_in_added_text() -> None:
    old = "<R>\r\n</R>\r\n"
    new = "<R>\r\n\t<A>\r\n\t\t<!-- note -->\r\n\t\t<B/>\r\n\t</A>\r\n</R>\r\n"
    change = _diff(old, new)["elements"][0]
    assert change["xml"] == "\t<A>\r\n\t\t<!-- note -->\r\n\t\t<B/>\r\n\t</A>"
    assert change["indentation"] == "\\t"


def test_diff_of_utf16_document_and_changed_declaration() -> None:
    old = '<?xml version="1.0" encoding="UTF-8"?>\n<R><A>1</A></R>\n'
    new = '<?xml version="1.0" encoding="UTF-16" standalone="no"?>\n<R><A>1</A><B>é</B></R>\n'
    result = diff_xml("x.xml", old.encode("utf-8"), new.encode("utf-16"), Names())
    assert result["bytes"]["changed"] == ["declaration", "bom", "declared_encoding"]
    assert [(e["change"], e["xml"]) for e in result["elements"]] == [("added", "<B>é</B>")]


def test_diff_long_added_element_is_cut_and_flagged() -> None:
    body = "".join(f"<I>{number}</I>" for number in range(5000))
    result = diff_xml("x.xml", b"<R></R>", f"<R><BIG>{body}</BIG></R>".encode(), Names(), 100)
    change = result["elements"][0]
    assert change["xml_cut"] is True and len(change["xml"]) == 100
    assert change["xml_characters"] > 40000


def test_parse_tree_reports_position_of_a_syntax_error() -> None:
    root, problem = parse_tree(b"<a><b></a>")
    assert root is None and problem is not None
    assert (problem.kind, problem.message, problem.line) == ("malformed", "mismatched tag", 1)
