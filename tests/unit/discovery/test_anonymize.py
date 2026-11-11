"""--anonymize: stable tokens, and no original name anywhere in what is sent back."""

from __future__ import annotations

import csv
import re
from pathlib import Path

import pytest

from porttool.discovery.names import Names, looks_like_path, split_extension, text_shape
from porttool.discovery.runner import GoldenDiffOptions, run_golden_diff
from porttool.discovery.tools import redact
from tests.unit.discovery import synth
from tests.unit.discovery.conftest import Runner, all_output_text, codes
from tests.unit.discovery.test_golden import _reference_change

#: ECUC module configurations are named after their standard module definition. These
#: three words are AUTOSAR/Vector names, visible in the kept /MICROSAR/... paths.
STANDARD_WORDS = {"Rte", "Os", "EcuC"}
_SHORT_NAME_RE = re.compile(r"<SHORT-NAME>([^<]+)</SHORT-NAME>")
_TOKEN_RE = re.compile(r"^N[0-9a-f]{6,}$")


def _secret_words(extra_files: dict[str, str] | None = None) -> set[str]:
    """Every short name, file stem and folder name of the synthetic repo."""
    files = dict(synth.FILES)
    files.update(extra_files or {})
    words = set(synth.SECRET_NAMES)
    for rel, content in files.items():
        words.update(_SHORT_NAME_RE.findall(content))
        segments = rel.split("/")
        words.update(segments[:-1])
        words.add(split_extension(segments[-1])[0])
    words.update(re.findall(r"[A-Za-z_]+(?=\.arxml)", synth.dcf_text()))
    return {word for word in words if word and word not in STANDARD_WORDS and word != ".gitignore"}


def _leaks(text: str, words: set[str]) -> list[str]:
    found = []
    for word in sorted(words):
        if re.search(r"(?<![A-Za-z0-9_])" + re.escape(word) + r"(?![A-Za-z0-9_])", text):
            found.append(word)
    return found


def _prepare(repo: Path, tmp_path: Path) -> dict[str, object]:
    synth.write(repo, "Zusätzlich/Größe.arxml", synth.SWC_BETA.replace("SwcBeta", "SwcUmlaut"))
    synth.write(repo, "Bad/Truncated.arxml", synth.SWC_ALPHA[:900])
    synth.write(repo, "Sip/DaVinciConfigurator/Core/DVCfgCmd.py", synth.FAKE_GOOD)
    synth.write(tmp_path / "bin", "DVImEx.py", synth.FAKE_NONZERO)
    synth.git_init(repo, branch="feature/secret-branch")
    return {
        "anonymize": True,
        "no_tools": False,
        "developer_bin": str(tmp_path / "bin"),
        "tool_suffixes": (".py",),
        "tool_launcher": synth.launcher,
        "tool_timeout_s": 20.0,
        "environ": {},
    }


def test_anonymize_no_original_name_appears_anywhere_in_the_output(
    repo: Path, tmp_path: Path, discover: Runner
) -> None:
    options = _prepare(repo, tmp_path)
    result = discover(repo, **options)
    assert all(step["ok"] for step in result.report["steps"])
    text = all_output_text(result.out_dir)
    words = _secret_words() | {"SwcUmlaut", "Größe", "Bad", "Truncated", "Sip", "secret"}
    assert _leaks(text, words) == []
    assert str(repo) not in text and str(tmp_path) not in text
    # the structure survives: tags, extensions, counts, standard definition paths
    report = result.report
    assert report["mode"]["anonymized"] is True
    assert report["inventory"]["top_level_kinds"]["APPLICATION-SW-COMPONENT-TYPE"]["objects"] == 4
    assert report["ecuc"]["rte"][0]["definition"] == "/MICROSAR/Rte"
    assert report["ecuc"]["summary"]["events_mapped_to_a_task"] == 2
    assert report["xml_style"]["overall"]["admin_data"]["sdg_gids"] == {"DV:DEV": 2}
    files = [row["file"] for row in report["inventory"]["arxml_files"]]
    assert all(re.fullmatch(r"(N[0-9a-f]+/)*N[0-9a-f]+(\.ecuc)?\.arxml", name) for name in files)
    assert _TOKEN_RE.match(report["git"]["branch"])
    assert "DVCfgCmd" in text and "DaVinciConfigurator/Core" in text  # product names stay
    assert "Mode: ANONYMIZED" in (Path(result.out_dir) / "discovery.md").read_text("utf-8")
    assert "ANONYMIZED" in (Path(result.out_dir) / "README.txt").read_text("utf-8")


def test_the_leak_detector_itself_finds_names_in_a_report_that_is_not_anonymized(
    repo: Path, discover: Runner
) -> None:
    synth.git_init(repo, branch="feature/secret-branch")
    result = discover(repo)
    found = _leaks(all_output_text(result.out_dir), _secret_words())
    assert {"SwcAlpha", "SwcModel", "DevWs", "EcuComposition", "IfSpeed", "EcuProj",
            "GoneAway", "MyDataTypes", "DetService", "feature/secret-branch"} <= set(found)  # fmt: skip


def test_anonymize_golden_diff_has_no_original_name_either(repo: Path, tmp_path: Path) -> None:
    base, head = _reference_change(repo)
    result = run_golden_diff(
        GoldenDiffOptions(repo=str(repo), base=base, head=head, anonymize=True)
    )
    text = all_output_text(result.out_dir)
    words = _secret_words({"x/IfNew.arxml": synth.interface("IfNew")}) | {"PpNew", "Notes"}
    assert _leaks(text, words) == []
    files = {item["status"]: item for item in result.report["golden_diff"]["files"] if item["xml"]}
    added_file = files["added"]["elements"][0]
    assert added_file["xml_is_exact_source_text"] is False
    xml = added_file["xml"]
    # structure, numbers, booleans, enumeration-like values and standard namespaces stay
    assert xml.startswith('<AUTOSAR xmlns="http://autosar.org/schema/r4.0"')
    assert "AUTOSAR_4-2-2.xsd" in xml
    assert "<IS-SERVICE>false</IS-SERVICE>" in xml
    assert 'DEST="IMPLEMENTATION-DATA-TYPE"' in xml
    assert re.search(r"<SHORT-NAME>N[0-9a-f]{6,}</SHORT-NAME>", xml)
    assert re.search(r'<TYPE-TREF DEST="[A-Z-]+">/N[0-9a-f]+/N[0-9a-f]+</TYPE-TREF>', xml)
    report_text = str(result.report)
    assert "/ComponentTypes" not in report_text


def test_anonymize_tokens_are_stable_within_a_run_and_across_runs(
    repo: Path, tmp_path: Path, discover: Runner
) -> None:
    options = _prepare(repo, tmp_path)
    options["no_tools"] = True
    first = discover(repo, **options)
    owners = first.report["owners"]
    model = owners["model_candidates"][0]
    naming = first.report["storage_map"]["kinds"]["APPLICATION-SW-COMPONENT-TYPE"]["file_naming"]
    sample_names = {sample["short_name"] for row in naming for sample in row["samples"]}
    assert model["swc"] in sample_names  # the same name gives the same token in every section
    assert model["path"].endswith("/" + model["swc"])
    assert model["file"].endswith("/" + model["swc"] + ".arxml")  # stem equals short name
    csv_rows = (Path(first.out_dir) / "owners_proposed.csv").read_text("utf-8").splitlines()
    assert any(row.startswith(model["swc"] + ",,model,Simulink,") for row in csv_rows)

    second = discover(repo, **options)
    assert second.report["owners"]["model_candidates"][0]["swc"] == model["swc"]
    assert second.report["git"]["branch"] == first.report["git"]["branch"]
    assert [r["file"] for r in second.report["inventory"]["arxml_files"]] == [
        r["file"] for r in first.report["inventory"]["arxml_files"]
    ]


def test_anonymize_private_folder_keeps_the_token_table_outside_the_report(
    repo: Path, tmp_path: Path, discover: Runner
) -> None:
    options = _prepare(repo, tmp_path)
    options["no_tools"] = True
    result = discover(repo, **options)
    assert result.private_dir == result.out_dir + "_private"
    private = Path(result.private_dir)
    with open(private / "token_map.csv", encoding="utf-8", newline="") as handle:
        table = dict(row for row in csv.reader(handle))
    token = result.report["owners"]["model_candidates"][0]["swc"]
    assert table[token] == "SwcModel"
    assert "SwcModel,,model,Simulink" in (private / "owners_proposed.csv").read_text("utf-8")
    assert (private / "locked_proposed.txt").read_text("utf-8") == "DetService\n"
    assert not (Path(result.out_dir) / "token_map.csv").exists()
    assert f"Do NOT send: {result.private_dir}" in result.summary
    assert "_private" in (Path(result.out_dir) / "README.txt").read_text("utf-8")
    # neither folder is scanned or counted as a change by a later run
    again = discover(repo, **options)
    assert again.report["git"]["clean"] is True
    assert again.report["inventory"]["by_type"]["arxml"]["count"] == 14


def test_anonymize_without_a_usable_private_folder_still_reports(
    repo: Path, tmp_path: Path, discover: Runner
) -> None:
    out = tmp_path / "report"
    (tmp_path / "report_private").write_text("a file blocks the folder", encoding="utf-8")
    result = discover(repo, anonymize=True, out=str(out))
    assert "DISC-PRIVATE-WRITE" in codes(result)
    assert _leaks(all_output_text(out), _secret_words()) == []


def test_mixed_modes_in_one_folder_are_flagged_in_the_readme(repo: Path, discover: Runner) -> None:
    synth.git_init(repo)
    discover(repo)
    result = discover(repo, anonymize=True)
    base = synth.git(repo, "rev-parse", "HEAD")
    run_golden_diff(GoldenDiffOptions(repo=str(repo), base=base, head=base))
    readme = (Path(result.out_dir) / "README.txt").read_text("utf-8")
    assert "discovery.json: ANONYMIZED" in readme
    assert "NOT anonymized" in readme
    assert "WARNING: this folder holds anonymized AND non-anonymized reports" in readme


# ---- the name mapper


def test_names_identity_when_not_anonymizing() -> None:
    names = Names()
    assert names.name("SwcA") == "SwcA" and names.ar_path("/A/B") == "/A/B"
    assert names.fs_path("C:\\x\\y.arxml") == "C:\\x\\y.arxml"
    assert names.value("anything") == "anything" and names.external("msg") == "msg"
    assert names.mapping() == {}


def test_names_tokens_keep_structure_extensions_and_separators() -> None:
    names = Names(anonymize=True, key=b"k")
    token = names.name("SwcA")
    assert _TOKEN_RE.match(token) and names.name("SwcA") == token
    assert names.name("SwcB") != token and names.name("") == ""
    assert names.ar_path("/Pkg/SwcA") == f"/{names.name('Pkg')}/{token}"
    assert names.rel_path("Pkg/SwcA.arxml") == f"{names.name('Pkg')}/{token}.arxml"
    assert names.rel_path("Pkg/Sub", is_dir=True) == f"{names.name('Pkg')}/{names.name('Sub')}"
    assert names.file_name("Proj.ecuc.arxml") == names.name("Proj") + ".ecuc.arxml"
    assert names.file_name("~SwcA.tmp") == "~" + token + ".tmp"
    assert names.fs_path("..\\Pkg\\SwcA.arxml") == f"..\\{names.name('Pkg')}\\{token}.arxml"
    assert names.fs_path("C:/Pkg/./SwcA.dcf") == f"C:/{names.name('Pkg')}/./{token}.dcf"
    variable = names.fs_path("$(ProjectDir)\\%USER%\\SwcA.arxml")
    assert variable == f"$({names.name('ProjectDir')})\\%{names.name('USER')}%\\{token}.arxml"
    assert names.mapping()[token] == "SwcA"
    assert Names(anonymize=True, key=b"other").name("SwcA") != token


def test_names_values_keep_only_what_cannot_identify_the_project() -> None:
    names = Names(anonymize=True, key=b"k")
    for kept in ("42", "-1.5e3", "0x1F", "true", "4.2.2", "2026-01-31T10:00:00Z", "  ",
                 "3F2504E0-4F89-41D3-9A0C-0305E82C3301", "http://autosar.org/schema/r4.0"):  # fmt: skip
        assert names.value(kept) == kept
    assert _TOKEN_RE.match(names.value("COMPONENTTYPE"))  # strict: could be a project word
    assert names.value("NOT-ACCESSIBLE", allow_enum=True) == "NOT-ACCESSIBLE"
    names.name("BCM")
    assert _TOKEN_RE.match(names.value("BCM", allow_enum=True))  # a known name never stays
    assert names.value("/Pkg/Type") == names.ar_path("/Pkg/Type")
    assert names.value("..\\a\\b.arxml") == names.fs_path("..\\a\\b.arxml")
    assert _TOKEN_RE.match(names.value("http://intranet.example/secret"))
    assert names.definition_ref("/MICROSAR/Rte/RteX") == "/MICROSAR/Rte/RteX"
    assert names.definition_ref("/Company/Cdd") == names.ar_path("/Company/Cdd")
    assert names.external("git said: secret") == "[text removed by --anonymize: 16 characters]"
    kept = names.tool_path("C:\\Program Files (x86)\\Vector DaVinci Developer 4.12\\Bin\\DVImEx.exe")
    assert kept == "C:\\Program Files (x86)\\Vector DaVinci Developer 4.12\\Bin\\DVImEx.exe"
    hidden = names.tool_path("D:\\Projects\\Secret\\Sip\\DaVinciConfigurator\\Core\\DVCfgCmd.exe")
    assert "Secret" not in hidden and hidden.endswith("\\DaVinciConfigurator\\Core\\DVCfgCmd.exe")


def test_token_collisions_get_longer_tokens_not_shared_ones() -> None:
    names = Names(anonymize=True, key=b"k")
    tokens = {names.name(f"Name{number}") for number in range(20000)}
    assert len(tokens) == 20000


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Config\\DevWs\\SwcA.arxml", True),
        ("SwcA.arxml", True),
        ("..\\Sip", True),
        ("C:\\Vector\\Sip", True),
        ("$(Root)/a.dcf", True),
        ("/DataTypes/uint8", False),
        ("/opt/x/file.arxml", True),
        ("http://autosar.org/schema/r4.0", False),
        ("4.2.2", False),
        ("01/02/2024", False),
        ("n/a", False),
        ("plain words", False),
        ("", False),
    ],
)
def test_looks_like_path(value: str, expected: bool) -> None:
    assert looks_like_path(value) is expected


def test_text_shape_and_redaction() -> None:
    assert text_shape("Abc-12 x") == "Aaa-99 a"
    assert text_shape("x" * 100).endswith("...")
    pairs = [("C:\\Work\\Repo", "<REPO>"), ("jdoe", "<USER>")]
    assert redact("log in c:\\work\\repo\\x by JDoe", pairs) == "log in <REPO>\\x by <USER>"
