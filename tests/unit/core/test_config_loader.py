"""Tests for porttool.core.config_loader: load, validate, save, discover."""

from __future__ import annotations

import dataclasses
import os
import tomllib
from pathlib import Path

import pytest

from porttool.core import config_loader, fsutil
from porttool.core.config import NamingRules, PackagePaths, ProjectConfig, ToolTimeouts
from porttool.core.config_loader import (
    discover_paths,
    load_config,
    load_locked,
    load_owners,
    render_config,
    save_config,
)
from porttool.core.errors import ConfigError, WriteError
from porttool.core.models import ArxmlOwner
from porttool.core.results import Severity

MINIMAL = '[paths]\narxml_roots = ["Config/Developer"]\n'


@pytest.fixture(autouse=True)
def _app_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))


def make_repo(tmp_path: Path, toml: str | None = MINIMAL, name: str = "repo") -> Path:
    repo = tmp_path / name
    (repo / "Config" / "Developer").mkdir(parents=True)
    if toml is not None:
        (repo / "porttool.toml").write_text(toml, encoding="utf-8")
    return repo


def codes(findings: tuple[object, ...]) -> list[str]:
    return [getattr(finding, "code", "") for finding in findings]


# --------------------------------------------------------------------------- porttool.toml


def test_minimal_config_gets_the_contract_defaults(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    loaded = load_config(repo)
    config = loaded.config
    assert loaded.config_file == repo / "porttool.toml"
    assert config.repo_root == repo
    assert config.arxml_roots == (repo / "Config" / "Developer",)
    assert config.dpa_file is None and config.ecuc_file is None
    assert config.naming == NamingRules()
    assert config.packages == PackagePaths()
    assert config.timeouts == ToolTimeouts()
    assert config.generate_modules == ("Rte", "Os")
    assert config.pending_file == "porttool/pending.json"
    assert config.cache_dir == fsutil.default_cache_dir(repo)
    assert config.owners == {} and config.locked_swcs == frozenset()
    # No owners.csv and no locked.txt: said clearly, but not fatal.
    assert codes(loaded.findings) == ["CFG-OWNERS-001", "CFG-LOCKED-001"]
    assert not loaded.has_errors


def test_full_config_is_read(tmp_path: Path) -> None:
    repo = make_repo(
        tmp_path,
        """
format_version = 1

[paths]
dpa_file = "Config/Ecu.dpa"
dcf_file = "Config/Developer/Ecu.dcf"
arxml_roots = ["Config/Developer", "Models"]
exclude_globs = ["Appl/GenData/**", "**/*.bak"]
ecuc_file = "Config/ECUC/Ecu_ecuc.arxml"
gen_data = "Appl/GenData"
cache_dir = "build/cache"

[naming]
max_length = 64
port = '^(Pp|Rp)[A-Z]\\w*$'
connector_pattern = "{provider}_{provider_port}__{requester}_{requester_port}"

[packages]
interfaces = "/Project/Interfaces"

[timeouts]
generate_s = 7200
git_s = 30.5

[generate]
modules = ["Rte"]

[pending]
file = "porttool/queue.json"
max_age_days = 30

[limits]
large_file_bytes = 1000
min_free_disk_bytes = 0
""",
    )
    for folder in ("Config/ECUC", "Appl/GenData", "Models"):
        (repo / folder).mkdir(parents=True)
    for file in ("Config/Ecu.dpa", "Config/Developer/Ecu.dcf", "Config/ECUC/Ecu_ecuc.arxml"):
        (repo / file).write_text("<x/>")
    loaded = load_config(repo)
    config = loaded.config
    assert config.dpa_file == repo / "Config" / "Ecu.dpa"
    assert config.dcf_file == repo / "Config" / "Developer" / "Ecu.dcf"
    assert config.arxml_roots == (repo / "Config" / "Developer", repo / "Models")
    assert config.exclude_globs == ("Appl/GenData/**", "**/*.bak")
    assert config.ecuc_file == repo / "Config" / "ECUC" / "Ecu_ecuc.arxml"
    assert config.gen_data == repo / "Appl" / "GenData"
    assert config.cache_dir == repo / "build" / "cache"
    assert config.naming.max_length == 64
    assert config.naming.port == r"^(Pp|Rp)[A-Z]\w*$"
    assert config.naming.connector_pattern.count("__") == 1
    assert config.naming.server_runnable_pattern == NamingRules().server_runnable_pattern
    assert config.packages.interfaces == "/Project/Interfaces"
    assert config.packages.data_types == PackagePaths().data_types
    assert config.timeouts.generate_s == 7200.0 and config.timeouts.git_s == 30.5
    assert config.timeouts.validate_s == ToolTimeouts().validate_s
    assert config.generate_modules == ("Rte",)
    assert config.pending_file == "porttool/queue.json"
    assert config.pending_max_age_days == 30
    assert config.large_file_bytes == 1000 and config.min_free_disk_bytes == 0
    assert codes(loaded.findings) == ["CFG-OWNERS-001", "CFG-LOCKED-001"]


def test_config_file_missing(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, toml=None)
    with pytest.raises(ConfigError) as caught:
        load_config(repo)
    assert str(repo / "porttool.toml") in caught.value.user_message
    assert "does not exist" in caught.value.user_message
    assert caught.value.code == "PT-E100"


def test_repo_folder_missing(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="repository folder .* does not exist"):
        load_config(tmp_path / "nowhere")


def test_malformed_toml(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, '[paths\narxml_roots = ["Config/Developer"]\n')
    with pytest.raises(ConfigError) as caught:
        load_config(repo)
    message = caught.value.user_message
    assert "is not valid TOML" in message and "line 1" in message
    assert str(repo / "porttool.toml") in message


def test_config_file_is_not_utf8(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, toml=None)
    (repo / "porttool.toml").write_bytes(b'[paths]\narxml_roots = ["Gr\xf6\xdfe"]\n')
    with pytest.raises(ConfigError, match="is not UTF-8"):
        load_config(repo)


def test_config_file_with_utf8_bom(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, toml=None)
    (repo / "porttool.toml").write_bytes(b"\xef\xbb\xbf" + MINIMAL.encode())
    assert load_config(repo).config.arxml_roots == (repo / "Config" / "Developer",)


@pytest.mark.parametrize(
    ("toml", "expected"),
    [
        ('[paths]\narxml_roots = "Config/Developer"\n', "[paths] arxml_roots must be a list"),
        ("[paths]\narxml_roots = [1]\n", "[paths] arxml_roots: entry 1 must be a non-empty text"),
        (MINIMAL + "dpa_file = 5\n", "[paths] dpa_file must be a text in quotes"),
        (MINIMAL + 'dpa_file = ""\n', "[paths] dpa_file must not be empty"),
        (MINIMAL + '[timeouts]\ngenerate_s = "long"\n', "[timeouts] generate_s must be a number"),
        (MINIMAL + "[timeouts]\ngit_s = true\n", "[timeouts] git_s must be a number"),
        (MINIMAL + "[timeouts]\ngit_s = 0\n", "[timeouts] git_s must be greater than 0"),
        (MINIMAL + "[naming]\nmax_length = 12.5\n", "[naming] max_length must be a whole number"),
        (MINIMAL + "[naming]\nmax_length = 0\n", "[naming] max_length must be at least 1"),
        (MINIMAL + "[naming]\nport = '^(Pp'\n", "[naming] port is not a valid regular expression"),
        (
            MINIMAL + '[naming]\nserver_runnable_pattern = "{port}_{op}"\n',
            "[naming] server_runnable_pattern uses the unknown placeholder {op}",
        ),
        (
            MINIMAL + '[naming]\nconnector_pattern = "{provider"\n',
            "[naming] connector_pattern is not a valid pattern",
        ),
        (MINIMAL + '[packages]\ndata_types = "DataTypes"\n', "[packages] data_types must be an"),
        (MINIMAL + '[generate]\nmodules = "Rte"\n', "[generate] modules must be a list"),
        (MINIMAL + '[pending]\nfile = "../x.json"\n', "[pending] file must be a path inside"),
        (MINIMAL + '[pending]\nfile = "C:/x.json"\n', "[pending] file must be a path inside"),
        (MINIMAL + "[pending]\nmax_age_days = -1\n", "[pending] max_age_days must be at least 0"),
        (MINIMAL + '[limits]\nlarge_file_bytes = "big"\n', "[limits] large_file_bytes must be"),
        ("timeouts = 5\n" + MINIMAL, "[timeouts] must be a table, but it is the number 5"),
        ("paths = 5\n", "[paths] must be a table"),
        ('format_version = "1"\n' + MINIMAL, "format_version must be a whole number"),
    ],
)
def test_wrong_types_and_values(tmp_path: Path, toml: str, expected: str) -> None:
    repo = make_repo(tmp_path, toml)
    with pytest.raises(ConfigError) as caught:
        load_config(repo)
    assert expected in caught.value.user_message
    assert str(repo / "porttool.toml") in caught.value.user_message


def test_all_problems_are_reported_together(tmp_path: Path) -> None:
    repo = make_repo(
        tmp_path, MINIMAL + '[timeouts]\ngenerate_s = "long"\n[naming]\nmax_length = false\n'
    )
    with pytest.raises(ConfigError) as caught:
        load_config(repo)
    message = caught.value.user_message
    assert "has 2 problems" in message
    assert '[timeouts] generate_s must be a number, but it is the text "long".' in message
    assert "[naming] max_length must be a whole number, but it is the truth value false" in message


def test_arxml_roots_missing(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, "[paths]\n")
    with pytest.raises(ConfigError, match=r"\[paths\] arxml_roots is missing or empty"):
        load_config(repo)
    (repo / "porttool.toml").write_text("")
    with pytest.raises(ConfigError, match=r"\[paths\] arxml_roots is missing or empty"):
        load_config(repo)


def test_newer_format_version_is_refused(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, "format_version = 2\n" + MINIMAL)
    with pytest.raises(ConfigError, match="format_version 2.*Update PortTool"):
        load_config(repo)


def test_pointing_to_moved_folders(tmp_path: Path) -> None:
    repo = make_repo(
        tmp_path,
        '[paths]\narxml_roots = ["Config/Developer", "Config/Moved"]\n'
        'ecuc_file = "Config/ECUC/Ecu_ecuc.arxml"\ngen_data = "Appl/GenData"\n'
        'dcf_file = "Config"\nsip_root = "porttool.toml"\n',
    )
    # A required ARXML folder that moved is fatal ...
    with pytest.raises(ConfigError) as caught:
        load_config(repo)
    assert str(repo / "Config" / "Moved") in caught.value.user_message
    assert "does not exist" in caught.value.user_message
    # ... unless the caller wants to show the configuration anyway (settings screen).
    loaded = load_config(repo, strict=False)
    assert loaded.has_errors
    by_code = {finding.code: finding for finding in loaded.findings}
    assert by_code["CFG-PATH-003"].severity is Severity.ERROR
    assert str(repo / "Config" / "Moved") in by_code["CFG-PATH-003"].message
    assert loaded.config.arxml_roots[1] == repo / "Config" / "Moved"
    # Optional files and folders that moved are warnings in both modes.
    (repo / "Config" / "Moved").mkdir()
    loaded = load_config(repo)
    assert not loaded.has_errors
    missing = [f.message for f in loaded.findings if f.code == "CFG-PATH-001"]
    assert len(missing) == 2
    assert any("ecuc_file" in text and "Ecu_ecuc.arxml" in text for text in missing)
    assert any("gen_data" in text for text in missing)
    wrong_kind = [f.message for f in loaded.findings if f.code == "CFG-PATH-002"]
    assert any("dcf_file must be a file" in text for text in wrong_kind)
    assert any("sip_root must be a folder" in text for text in wrong_kind)
    assert all(f.severity is Severity.WARNING for f in loaded.findings if "PATH" in f.code)
    assert loaded.config.ecuc_file == repo / "Config" / "ECUC" / "Ecu_ecuc.arxml"


def test_paths_with_spaces_and_non_ascii_characters(tmp_path: Path) -> None:
    repo = tmp_path / "Mein ECU Projekt (Größe) مشروع"
    workspace = repo / "Konfiguration Ä" / "Developer Arbeitsbereich"
    workspace.mkdir(parents=True)
    (repo / "porttool.toml").write_text(
        '[paths]\narxml_roots = ["Konfiguration Ä/Developer Arbeitsbereich"]\n'
        'dcf_file = "Konfiguration Ä/Developer Arbeitsbereich/Größe.dcf"\n',
        encoding="utf-8",
    )
    (workspace / "Größe.dcf").write_text("<DCF/>")
    (repo / "owners.csv").write_text(
        "SWC,team,arxml_owner\nSwcA,Team Ä,davinci\n", encoding="utf-8"
    )
    loaded = load_config(repo)
    assert loaded.config.arxml_roots == (workspace,)
    assert loaded.config.dcf_file == workspace / "Größe.dcf"
    assert loaded.config.team_of("SwcA") == "Team Ä"
    assert codes(loaded.findings) == ["CFG-LOCKED-001"]
    # And the same configuration survives a save.
    save_config(loaded.config)
    assert load_config(repo).config == loaded.config


def test_absolute_paths_and_dot_segments(tmp_path: Path) -> None:
    outside = tmp_path / "Vector" / "SIP"
    outside.mkdir(parents=True)
    repo = make_repo(
        tmp_path,
        '[paths]\narxml_roots = ["./Config/../Config/Developer"]\n'
        f'sip_root = "{outside.as_posix()}"\n',
    )
    config = load_config(repo).config
    assert config.arxml_roots == (repo / "Config" / "Developer",)
    assert config.sip_root == outside


def test_explicit_config_file_location(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, toml=None)
    elsewhere = tmp_path / "settings"
    elsewhere.mkdir()
    (elsewhere / "my.toml").write_text(MINIMAL)
    (elsewhere / "locked.txt").write_text("SwcX\n")
    loaded = load_config(repo, config_file=elsewhere / "my.toml")
    assert loaded.config_file == elsewhere / "my.toml"
    # Relative paths still resolve against the repo root, not the config file's folder.
    assert loaded.config.arxml_roots == (repo / "Config" / "Developer",)
    assert loaded.config.locked_swcs == {"SwcX"}


def test_unknown_keys_and_tables_are_findings(tmp_path: Path) -> None:
    repo = make_repo(
        tmp_path,
        'colour = "blue"\n' + MINIMAL + 'arxml_root = "typo"\n'
        '[timeouts]\ngenerate = 5\n[gui]\ntheme = "dark"\n',
    )
    loaded = load_config(repo)
    unknown = [f for f in loaded.findings if f.code == "CFG-KEY-001"]
    assert len(unknown) == 4
    assert all(f.severity is Severity.WARNING for f in unknown)
    assert all(f.file == str(repo / "porttool.toml") for f in unknown)
    text = " | ".join(f.message for f in unknown)
    assert "[paths] arxml_root " in text and "[timeouts] generate " in text
    assert "table [gui]" in text and "setting colour" in text
    assert loaded.config.timeouts == ToolTimeouts()


# --------------------------------------------------------------------------- owners.csv


OWNERS = """SWC,team,arxml_owner,authoring_tool,export_folder
SwcA,Alpha,davinci,,
SwcB,Beta,,,
SwcM,Gamma,model,Simulink,Models/SwcM/arxml
SwcT,Gamma,MODEL,TargetLink,Models\\SwcT\\arxml\\
SwcL,Supplier,locked,,
"""


def test_owners_csv_is_read(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    (repo / "owners.csv").write_text(OWNERS, encoding="utf-8")
    (repo / "Models" / "SwcM" / "arxml").mkdir(parents=True)
    loaded = load_config(repo)
    config = loaded.config
    assert set(config.owners) == {"SwcA", "SwcB", "SwcM", "SwcT", "SwcL"}
    assert config.owner_of("SwcA") is ArxmlOwner.DAVINCI
    assert config.owner_of("SwcB") is ArxmlOwner.DAVINCI  # empty means davinci
    assert config.owner_of("SwcM") is ArxmlOwner.MODEL
    assert config.owner_of("SwcT") is ArxmlOwner.MODEL  # case does not matter
    assert config.owner_of("SwcL") is ArxmlOwner.LOCKED
    assert config.owner_of("Unlisted") is ArxmlOwner.DAVINCI
    assert config.team_of("SwcM") == "Gamma"
    assert config.owners["SwcM"].authoring_tool == "Simulink"
    assert config.owners["SwcM"].export_folder == "Models/SwcM/arxml"
    assert config.owners["SwcT"].export_folder == "Models/SwcT/arxml"  # normalised
    assert config.owners["SwcA"].authoring_tool is None
    assert config.owners["SwcA"].export_folder is None
    with pytest.raises(TypeError):
        config.owners["New"] = config.owners["SwcA"]  # type: ignore[index]
    # SwcT's export folder does not exist: a warning, not an error.
    assert codes(loaded.findings) == ["CFG-OWNERS-005", "CFG-LOCKED-001"]
    assert "Models/SwcT/arxml" in loaded.findings[0].message


def test_owners_csv_from_excel_with_semicolons_and_bom(tmp_path: Path) -> None:
    path = tmp_path / "owners.csv"
    path.write_bytes(
        b"\xef\xbb\xbf"
        + "Swc;Team;ARXML_Owner;Authoring_Tool;Export_Folder\r\n"
        "SwcM;Team Ä, Nord;model;Simulink;Models/SwcM\r\n\r\n;;;;\r\n".encode()
    )
    owners, findings = load_owners(path)
    assert owners["SwcM"].team == "Team Ä, Nord"
    assert owners["SwcM"].arxml_owner is ArxmlOwner.MODEL
    assert findings == ()


def test_owners_csv_with_tabs_quotes_comments_and_column_order(tmp_path: Path) -> None:
    path = tmp_path / "owners.csv"
    path.write_text(
        '# maintained by the integration team\narxml_owner\tSWC\tteam\n"model"\tSwcM\t"A ""B"""\n'
    )
    owners, findings = load_owners(path)
    assert owners["SwcM"].arxml_owner is ArxmlOwner.MODEL and owners["SwcM"].team == 'A "B"'
    assert codes(findings) == ["CFG-OWNERS-004"]  # model-owned without export folder


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("", "the file is empty"),
        ("team,arxml_owner\nAlpha,model\n", "no column 'swc'"),
        ("SWC,team\nSwcA,Alpha\n", "no column 'arxml_owner'"),
        ("SWC,arxml_owner,SWC\nSwcA,model,SwcB\n", "column 'swc' more than once"),
        ("SWC,arxml_owner\nSwcA,simulink\n", 'line 2: arxml_owner "simulink" of SWC SwcA is not'),
        ("SWC,arxml_owner\nSwc A,model\n", 'line 2: "Swc A" is not a valid SWC name'),
        ("SWC,arxml_owner\n,model\n", 'line 2: "" is not a valid SWC name'),
        ("SWC,arxml_owner\nSwcA,model,extra\n", "line 2 has 3 values, but the header has 2"),
        (
            "SWC,arxml_owner\nSwcA,model\nSwcB,\nSwcA,davinci\n",
            "SWC SwcA is listed twice with different values (lines 2 and 4)",
        ),
    ],
)
def test_owners_csv_problems_name_file_and_line(
    tmp_path: Path, content: str, expected: str
) -> None:
    path = tmp_path / "owners.csv"
    path.write_text(content)
    with pytest.raises(ConfigError) as caught:
        load_owners(path)
    assert expected in caught.value.user_message
    assert str(path) in caught.value.user_message


def test_owners_csv_duplicates_and_unknown_columns_are_findings(tmp_path: Path) -> None:
    path = tmp_path / "owners.csv"
    path.write_text("SWC,arxml_owner,notes\nSwcA,davinci,x\nSwcA,davinci,y\n")
    owners, findings = load_owners(path)
    assert set(owners) == {"SwcA"}
    assert codes(findings) == ["CFG-OWNERS-002", "CFG-OWNERS-003"]
    assert "'notes'" in findings[0].message
    assert "lines 2 and 3" in findings[1].message


def test_owners_csv_is_not_utf8(tmp_path: Path) -> None:
    path = tmp_path / "owners.csv"
    path.write_bytes(b"SWC,team,arxml_owner\nSwcA,Gr\xf6\xdfe,model\n")
    with pytest.raises(ConfigError, match="is not UTF-8"):
        load_owners(path)


def test_broken_owners_csv_stops_the_load(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    (repo / "owners.csv").write_text("SWC,arxml_owner\nSwcA,nobody\n")
    with pytest.raises(ConfigError, match="owners.csv"):
        load_config(repo)


# --------------------------------------------------------------------------- locked.txt


def test_locked_txt_is_read(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    (repo / "locked.txt").write_bytes(
        b"\xef\xbb\xbf# supplier components\r\nBswM\r\n\r\n"
        b"  Dcm   # diagnostics\r\nBswM\r\n#Off\r\n"
    )
    (repo / "owners.csv").write_text("SWC,arxml_owner\nDcm,davinci\n")
    loaded = load_config(repo)
    assert loaded.config.locked_swcs == {"BswM", "Dcm"}
    assert loaded.config.owner_of("Dcm") is ArxmlOwner.LOCKED  # locked.txt wins
    assert loaded.findings == ()


def test_locked_txt_with_an_invalid_name(tmp_path: Path) -> None:
    path = tmp_path / "locked.txt"
    path.write_text("SwcA\nSwc B, SwcC\n")
    with pytest.raises(ConfigError) as caught:
        load_locked(path)
    assert 'line 2: "Swc B, SwcC" is not a valid SWC name' in caught.value.user_message


def test_locked_txt_missing_when_loaded_directly(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="does not exist"):
        load_locked(tmp_path / "locked.txt")


# --------------------------------------------------------------------------- save


def test_save_config_round_trip(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    outside = tmp_path / "Vector SIP"
    outside.mkdir()
    for folder in ("Config/ECUC", "Appl/GenData"):
        (repo / folder).mkdir(parents=True)
    for file in ("Config/Ecu.dpa", "Config/Developer/Ecu.dcf", "Config/ECUC/Ecu_ecuc.arxml"):
        (repo / file).write_text("<x/>")
    config = ProjectConfig(
        repo_root=repo,
        dpa_file=repo / "Config" / "Ecu.dpa",
        dcf_file=repo / "Config" / "Developer" / "Ecu.dcf",
        arxml_roots=(repo / "Config" / "Developer",),
        exclude_globs=("Appl/GenData/**",),
        ecuc_file=repo / "Config" / "ECUC" / "Ecu_ecuc.arxml",
        sip_root=outside,
        gen_data=repo / "Appl" / "GenData",
        cache_dir=fsutil.default_cache_dir(repo),
        naming=NamingRules(
            max_length=40, port=r"^(Pp|Rp)[A-Z]\w*$", runnable='it\'s "quoted"\\d\tTab'
        ),
        packages=PackagePaths(constants="/Project/Constants"),
        timeouts=ToolTimeouts(generate_s=10.0, git_s=0.5),
        generate_modules=("Rte", "Os", "Com"),
        pending_file="porttool/p.json",
        pending_max_age_days=3,
        large_file_bytes=123,
        min_free_disk_bytes=456,
    )
    target = save_config(config)
    assert target == repo / "porttool.toml"
    text = target.read_text(encoding="utf-8")
    assert "\r" not in text
    assert 'dpa_file = "Config/Ecu.dpa"' in text  # repo-relative, forward slashes
    assert f'sip_root = "{outside.as_posix()}"' in text  # outside the repo: absolute
    assert "cache_dir" not in text  # the default is not pinned into the file
    assert "port = '^(Pp|Rp)[A-Z]\\w*$'" in text  # regular expressions stay readable
    assert tomllib.loads(text)["naming"]["runnable"] == 'it\'s "quoted"\\d\tTab'
    loaded = load_config(repo)
    assert loaded.config == config
    assert [f.code for f in loaded.findings] == ["CFG-OWNERS-001", "CFG-LOCKED-001"]


def test_save_config_keeps_a_custom_cache_dir_and_target(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, toml=None)
    config = ProjectConfig(
        repo_root=repo, arxml_roots=(repo / "Config" / "Developer",), cache_dir=repo / ".cache"
    )
    target = save_config(config, tmp_path / "elsewhere.toml")
    assert target == tmp_path / "elsewhere.toml"
    assert 'cache_dir = ".cache"' in target.read_text()
    assert load_config(repo, config_file=target).config.cache_dir == repo / ".cache"


def test_save_config_is_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = make_repo(tmp_path)
    config = load_config(repo).config

    def locked(src: str, dst: str) -> None:
        raise PermissionError(13, "in use")

    monkeypatch.setattr(os, "replace", locked)
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    with pytest.raises(WriteError):
        save_config(dataclasses.replace(config, pending_max_age_days=99))
    assert (repo / "porttool.toml").read_text() == MINIMAL
    assert sorted(os.listdir(repo)) == ["Config", "porttool.toml"]


def test_render_config_rejects_unwritable_values(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot be written to TOML"):
        config_loader._toml_value(object())
    assert config_loader._toml_value(True) == "true"
    assert render_config(ProjectConfig(repo_root=tmp_path)).startswith("# PortTool")


# --------------------------------------------------------------------------- discovery

ECUC = "<AUTOSAR><ECUC-MODULE-CONFIGURATION-VALUES/></AUTOSAR>"
SWC = "<AUTOSAR><APPLICATION-SW-COMPONENT-TYPE/></AUTOSAR>"


def make_ecu_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "Ecu Repo Ä"
    for folder in (
        "Config/Developer/ComponentTypes",
        "Config/ECUC",
        "Config/System",
        "Appl/GenData",
        "Models/SwcM/arxml",
        ".git/objects",
    ):
        (repo / folder).mkdir(parents=True)
    (repo / "Config/Developer/Ecu.dcf").write_text("<DCF/>")
    (repo / "Config/Developer/ComponentTypes/SwcA.arxml").write_text(SWC)
    (repo / "Config/ECUC/Ecu_ecuc.arxml").write_text(ECUC)
    (repo / "Config/ECUC/Ecu_Rte_ecuc.arxml").write_text(ECUC)
    (repo / "Config/System/FlatExtract.arxml").write_text(SWC)
    (repo / "Config/System/FlatMap.arxml").write_text(SWC)
    (repo / "Appl/GenData/Rte_Generated.arxml").write_text(SWC)
    (repo / "Models/SwcM/arxml/SwcM.arxml").write_text(SWC)
    (repo / ".git/objects/x.arxml").write_text(SWC)
    (repo / "Config/Ecu.dpa").write_text(
        '<?xml version="1.0"?>\n<Project Version="5">\n'
        "  <Name>Ecu</Name>\n  <Url>http://example.invalid/a/b</Url>\n"
        "  <Folders><GenData>..\\Appl\\GenData</GenData><Missing>.\\Nowhere</Missing></Folders>\n"
        '  <Developer Workspace=".\\Developer\\Ecu.dcf"/>\n'
        "  <ActiveEcuc>.\\ECUC\\Ecu_Rte_ecuc.arxml</ActiveEcuc>\n</Project>\n"
    )
    return repo


def test_discover_paths_reads_the_dpa_then_scans(tmp_path: Path) -> None:
    repo = make_ecu_repo(tmp_path)
    found = discover_paths(repo, repo / "Config" / "Ecu.dpa")
    assert found.repo_root == repo
    assert found.dpa_files == (repo / "Config" / "Ecu.dpa",)
    assert found.dcf_files == (repo / "Config" / "Developer" / "Ecu.dcf",)
    # The ECUC file named by the .dpa comes first, the one found by the scan second.
    assert found.ecuc_files == (
        repo / "Config" / "ECUC" / "Ecu_Rte_ecuc.arxml",
        repo / "Config" / "ECUC" / "Ecu_ecuc.arxml",
    )
    assert found.flat_extracts == (repo / "Config" / "System" / "FlatExtract.arxml",)
    assert found.flat_maps == (repo / "Config" / "System" / "FlatMap.arxml",)
    assert found.gen_data_dirs == (repo / "Appl" / "GenData",)
    assert found.arxml_roots == (repo / "Config" / "Developer",)
    assert [f.code for f in found.findings] == ["DISC-ARXML-001"]
    assert found.findings[0].severity is Severity.INFO


def test_discovered_proposal_saves_and_loads(tmp_path: Path) -> None:
    repo = make_ecu_repo(tmp_path)
    proposal = discover_paths(repo).propose()  # the only .dpa in the repo is found by the scan
    assert proposal.dpa_file == repo / "Config" / "Ecu.dpa"
    assert proposal.ecuc_file == repo / "Config" / "ECUC" / "Ecu_Rte_ecuc.arxml"
    assert proposal.exclude_globs == ("Appl/GenData/**",)
    save_config(proposal)
    loaded = load_config(repo)
    assert loaded.config == proposal
    assert not [f for f in loaded.findings if "PATH" in f.code]


def test_discover_paths_without_dpa_and_dcf(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    for folder in ("a/b/c", "a/b/d", "x"):
        (repo / folder).mkdir(parents=True)
    (repo / "a/b/one.arxml").write_text(SWC)
    (repo / "a/b/c/two.arxml").write_text(SWC)
    (repo / "x/three.ARXML").write_text(SWC)
    (repo / "x/Module_ecuc.arxml").write_text(ECUC)
    found = discover_paths(repo)
    assert found.dpa_files == () and found.dcf_files == ()
    assert found.arxml_roots == (repo / "a" / "b", repo / "x")  # a/b/c lies below a/b
    assert found.ecuc_files == (repo / "x" / "Module_ecuc.arxml",)
    assert [f.code for f in found.findings] == ["DISC-DPA-003", "DISC-DCF-001"]
    proposal = found.propose()
    assert proposal.dpa_file is None and proposal.gen_data is None
    assert proposal.exclude_globs == ()


def test_discover_paths_in_an_empty_repo(tmp_path: Path) -> None:
    found = discover_paths(tmp_path)
    assert found.arxml_roots == ()
    assert [f.code for f in found.findings] == [
        "DISC-DPA-003",
        "DISC-DCF-001",
        "DISC-ECUC-001",
        "DISC-ARXML-002",
    ]
    assert all(f.severity is Severity.WARNING for f in found.findings)


def test_discover_paths_with_a_dpa_that_is_not_xml(tmp_path: Path) -> None:
    repo = make_ecu_repo(tmp_path)
    (repo / "Config" / "Ecu.dpa").write_bytes(b"\x00\x01 not xml at all")
    found = discover_paths(repo)
    assert "DISC-DPA-001" in [f.code for f in found.findings]
    # The scan still finds everything; the two ECUC files are now equally likely.
    assert found.dcf_files == (repo / "Config" / "Developer" / "Ecu.dcf",)
    assert len(found.ecuc_files) == 2
    assert "DISC-ECUC-002" in [f.code for f in found.findings]


def test_discover_paths_with_several_dpa_and_dcf_files(tmp_path: Path) -> None:
    repo = make_ecu_repo(tmp_path)
    (repo / "Config" / "Other.dpa").write_text("<Project/>")
    (repo / "Config" / "Developer" / "Backup.dcf").write_text("<DCF/>")
    found = discover_paths(repo)
    assert len(found.dpa_files) == 2 and len(found.dcf_files) == 2
    found_codes = [f.code for f in found.findings]
    assert "DISC-DPA-002" in found_codes and "DISC-DCF-002" in found_codes


def test_discover_paths_with_missing_inputs(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="repository folder"):
        discover_paths(tmp_path / "nowhere")
    with pytest.raises(ConfigError, match="project file .* does not exist"):
        discover_paths(tmp_path, tmp_path / "gone.dpa")
