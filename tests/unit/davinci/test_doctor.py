"""doctor and GUI detection: no tool is started, every fix names its porttool.toml line."""

from __future__ import annotations

import struct
import tomllib
from pathlib import Path
from types import MappingProxyType

import psutil
import pytest

from porttool.core.config import ProjectConfig
from porttool.core.results import Finding, Severity
from porttool.davinci import DaVinciToolRunner, davinci_gui_findings
from porttool.davinci.doctor import doctor_findings
from porttool.davinci.executables import find_executable, parse_file_version, read_file_version
from porttool.davinci.settings import ACTIONS, BUILTIN_COMMANDS, load_settings
from tests.unit.davinci.helpers import FakeCtx, codes, fake_vars, make_config, run


def _no_processes() -> list[tuple[str, int]]:
    return []


def _by_code(findings: tuple[Finding, ...], code: str) -> list[Finding]:
    return [finding for finding in findings if finding.code == code]


def _toml_line(message: str) -> dict[str, object]:
    """The porttool.toml line a finding tells the user to add, parsed as TOML."""
    return tomllib.loads(message.split("add the line: ")[1].split(" Needed by")[0])


def _version_resource(major: int, minor: int, build: int, patch: int) -> bytes:
    fixed = struct.pack("<4I", 0xFEEF04BD, 0x00010000, (major << 16) | minor, (build << 16) | patch)
    return b"MZ" + b"\0" * 100 + fixed + b"\0" * 40


def test_doctor_on_an_unconfigured_project_lists_what_to_add(tmp_path: Path) -> None:
    runner = DaVinciToolRunner(ProjectConfig(repo_root=tmp_path), log_dir=tmp_path / "logs")
    findings = doctor_findings(runner, processes=_no_processes)

    missing = _by_code(findings, "DVR-EXE-MISSING")
    assert [f.object_path for f in missing] == [
        "davinci.executables.dvwspchecker",
        "davinci.executables.dvimex",
        "davinci.executables.dvswcgen",
        "davinci.executables.dvcfgcmd",
    ]
    assert all(f.severity is Severity.WARNING for f in missing)  # no DaVinci step is enabled
    assert "neither developer_bin nor sip_root is set" in missing[0].message
    assert "table [davinci.executables], add the line: dvwspchecker = " in missing[0].message
    assert _toml_line(missing[3].message) == {"dvcfgcmd": r"C:\full\path\to\DVCfgCmd.exe"}
    assert "Needed by the action(s): script_task, validate, generate" in missing[3].message

    unverified = _by_code(findings, "DVR-TPL-UNVERIFIED")
    assert [f.object_path for f in unverified] == [f"davinci.commands.{a}" for a in ACTIONS]
    for action, finding in zip(ACTIONS, unverified, strict=True):
        assert "UNVERIFIED" in finding.message
        assert "table [davinci.commands], add the line: " in finding.message
        assert _toml_line(finding.message) == {action: list(BUILTIN_COMMANDS[action])}

    not_set = _by_code(findings, "DVR-PATH-NOT-SET")
    assert [f.object_path for f in not_set] == ["paths.dcf_file", "paths.dpa_file"]
    assert "table [paths], add the line: dcf_file = 'path/to/Workspace.dcf'" in not_set[0].message
    assert "check, import, contract_headers" in not_set[0].message

    assert findings[-1].code == "DVR-DOC-NO-RUN"
    assert not any(f.severity is Severity.ERROR for f in findings)


def test_doctor_makes_missing_things_errors_for_enabled_steps(tmp_path: Path) -> None:
    config = ProjectConfig(
        repo_root=tmp_path,
        dcf_file=tmp_path / "gone.dcf",
        extras=MappingProxyType({"pipeline": {"davinci_steps": ["check"]}}),
    )
    findings = doctor_findings(
        DaVinciToolRunner(config, log_dir=tmp_path / "logs"), processes=_no_processes
    )
    severities = {f.object_path: f.severity for f in _by_code(findings, "DVR-EXE-MISSING")}
    assert severities["davinci.executables.dvwspchecker"] is Severity.ERROR
    assert severities["davinci.executables.dvcfgcmd"] is Severity.WARNING
    (missing,) = _by_code(findings, "DVR-PATH-MISSING")
    assert missing.severity is Severity.ERROR and str(tmp_path / "gone.dcf") in missing.message


def test_doctor_with_everything_in_place_reports_tools_and_runs_nothing(tmp_path: Path) -> None:
    bin_dir = tmp_path / "dev bin"
    bin_dir.mkdir()
    (bin_dir / "DVWspChecker.exe").write_bytes(_version_resource(4, 12, 30, 1))
    (bin_dir / "dvimex.EXE").write_bytes(b"no version resource")
    record = tmp_path / "record.jsonl"
    config = make_config(
        tmp_path,
        davinci={"executables": {"dvcfgcmd": str(bin_dir / "DVWspChecker.exe")}},
        env=fake_vars(record_file=record),
        developer_bin=bin_dir,
    )
    findings = run(DaVinciToolRunner(config).doctor(FakeCtx()))
    assert not record.exists()  # doctor starts no tool
    exes = {f.object_path: f.message for f in _by_code(findings, "DVR-DOC-EXE")}
    assert "file version: 4.12.30.1" in exes["davinci.executables.dvwspchecker"]
    assert "(from developer_bin)" in exes["davinci.executables.dvwspchecker"]
    assert "file version: not readable" in exes["davinci.executables.dvimex"]
    assert "(from porttool.toml)" in exes["davinci.executables.dvcfgcmd"]
    # The test templates do not use {exe}, so the tool that is not there is not missed.
    assert _by_code(findings, "DVR-EXE-MISSING") == []
    assert len(_by_code(findings, "DVR-DOC-TPL")) == len(ACTIONS)
    assert _by_code(findings, "DVR-TPL-UNVERIFIED") == []
    assert [f.object_path for f in _by_code(findings, "DVR-DOC-PATH")] == [
        "paths.dcf_file",
        "paths.dpa_file",
    ]
    assert (
        f"Tool logs are written to {tmp_path / 'logs'}"
        in _by_code(findings, "DVR-DOC-LOG")[0].message
    )


def test_doctor_reports_setting_errors_and_placeholders_an_action_never_gets(
    tmp_path: Path,
) -> None:
    commands = {"check": ["{exe}", "{arxml}"], "generate": "one text"}
    config = make_config(tmp_path, commands=commands)
    findings = doctor_findings(DaVinciToolRunner(config), processes=_no_processes)
    assert findings[0].code == "DVR-TPL-NOT-LIST"
    (unresolved,) = _by_code(findings, "DVR-TPL-UNRESOLVED")
    assert "{arxml}" in unresolved.message and unresolved.object_path == "davinci.commands.check"


def test_doctor_reports_a_missing_script_and_an_unwritable_log_folder(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("file", encoding="utf-8")
    config = ProjectConfig(
        repo_root=tmp_path,
        extras=MappingProxyType(
            {"davinci": {"script_dir": "no/such/folder", "log_dir": str(blocker / "logs")}}
        ),
    )
    findings = doctor_findings(DaVinciToolRunner(config), processes=_no_processes)
    (script,) = _by_code(findings, "DVR-SCRIPT-FILE-MISSING")
    assert "PortToolTasks.dvgroovy" in script.message
    (log,) = _by_code(findings, "DVR-LOG-DIR")
    assert log.severity is Severity.ERROR and "add the line: log_dir = " in log.message


def test_doctor_includes_gui_findings(tmp_path: Path) -> None:
    runner = DaVinciToolRunner(make_config(tmp_path))
    findings = doctor_findings(runner, processes=lambda: [("DaVinciDEV.exe", 4711)])
    assert "DVR-GUI-RUNNING" in codes(findings)


# ------------------------------------------------------------------ executables


def test_executable_search_follows_the_discovery_folders(tmp_path: Path) -> None:
    sip = tmp_path / "SIP"
    core = sip / "DaVinciConfigurator" / "Core"
    core.mkdir(parents=True)
    (core / "DVCfgCmd.exe").write_bytes(b"x")
    dev = tmp_path / "Dev"
    (dev / "Core").mkdir(parents=True)
    (dev / "Core" / "DVImEx.exe").write_bytes(b"x")
    (dev / "DVSwcGen.exe").write_bytes(b"x")
    config = ProjectConfig(repo_root=tmp_path, developer_bin=dev, sip_root=sip)
    settings = load_settings(config)[0]
    cfg = find_executable("DVCfgCmd", settings, config)
    assert cfg.path == core / "DVCfgCmd.exe" and cfg.source == "sip_root"
    assert find_executable("DVImEx", settings, config).path == dev / "Core" / "DVImEx.exe"
    assert find_executable("DVSwcGen", settings, config).source == "developer_bin"
    missing = find_executable("DVWspChecker", settings, config)
    assert missing.path is None and len(missing.searched) == 6
    assert missing.searched[0] == str(dev / "DaVinciConfigurator" / "Core" / "DVWspChecker.exe")


def test_configured_executable_wins_and_may_be_a_folder_or_relative(tmp_path: Path) -> None:
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "DVCfgCmd.exe").write_bytes(b"x")
    for text in ("bin/DVCfgCmd.exe", "bin", str(tmp_path / "bin" / "DVCfgCmd.exe")):
        config = ProjectConfig(
            repo_root=tmp_path,
            extras=MappingProxyType({"davinci": {"executables": {"dvcfgcmd": text}}}),
        )
        lookup = find_executable("DVCfgCmd", load_settings(config)[0], config)
        assert lookup.path == tmp_path / "bin" / "DVCfgCmd.exe", text
        assert lookup.source == "porttool.toml"
    config = ProjectConfig(
        repo_root=tmp_path,
        extras=MappingProxyType({"davinci": {"executables": {"dvcfgcmd": "nowhere/x.exe"}}}),
    )
    lookup = find_executable("DVCfgCmd", load_settings(config)[0], config)
    assert lookup.path is None and lookup.configured == "nowhere/x.exe"


def test_file_version_reader_is_careful(tmp_path: Path) -> None:
    assert parse_file_version(_version_resource(5, 25, 40, 0)) == "5.25.40.0"
    assert parse_file_version(b"\xbd\x04\xef\xfe" + b"\0" * 12) is None  # wrong structure version
    assert parse_file_version(b"\xbd\x04\xef\xfe") is None
    assert parse_file_version(b"") is None
    assert read_file_version(tmp_path / "missing.exe") is None


# ------------------------------------------------------------------ GUI detection


def test_gui_process_running_is_reported_with_name_and_pid(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    listing = [("explorer.exe", 1), ("davincidev.EXE", 4711), ("DVCfg", 4712), ("python.exe", 3)]
    findings = davinci_gui_findings(config, processes=lambda: listing)
    assert codes(findings) == ["DVR-GUI-RUNNING", "DVR-GUI-RUNNING"]
    assert "davincidev.EXE is running (pid 4711)" in findings[0].message
    assert "may be open" in findings[0].message
    assert findings[1].object_path == "DVCfg"
    assert all(f.severity is Severity.WARNING for f in findings)


def test_gui_process_names_are_a_setting(tmp_path: Path) -> None:
    config = make_config(tmp_path, davinci={"gui_process_names": ["MyDev.exe"]})
    listing = [("DaVinciDEV.exe", 1), ("mydev.exe", 2)]
    findings = davinci_gui_findings(config, processes=lambda: listing)
    assert [f.object_path for f in findings] == ["mydev.exe"]


def test_lock_file_present_next_to_workspace_or_project(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    repo = config.repo_root
    (repo / "Workspace.dcf.lock").write_text("", encoding="utf-8")
    (repo / "~Project.dpa").write_text("", encoding="utf-8")
    (repo / "notes.txt").write_text("", encoding="utf-8")
    (repo / "folder.lock").mkdir()
    findings = davinci_gui_findings(config, processes=_no_processes)
    assert codes(findings) == ["DVR-GUI-LOCKFILE", "DVR-GUI-LOCKFILE"]
    assert {Path(f.file or "").name for f in findings} == {"Workspace.dcf.lock", "~Project.dpa"}
    assert "delete the lock file" in findings[0].message


def test_lock_file_globs_are_a_setting_and_no_sign_means_no_finding(tmp_path: Path) -> None:
    config = make_config(tmp_path, davinci={"lock_file_globs": ["*.inuse"]})
    (config.repo_root / "Workspace.dcf.lock").write_text("", encoding="utf-8")
    assert davinci_gui_findings(config, processes=_no_processes) == ()
    (config.repo_root / "x.inuse").write_text("", encoding="utf-8")
    assert codes(davinci_gui_findings(config, processes=_no_processes)) == ["DVR-GUI-LOCKFILE"]
    assert davinci_gui_findings(ProjectConfig(repo_root=tmp_path), processes=_no_processes) == ()


def test_process_scan_that_fails_is_a_finding(tmp_path: Path) -> None:
    def broken() -> list[tuple[str, int]]:
        raise psutil.AccessDenied()

    findings = davinci_gui_findings(make_config(tmp_path), processes=broken)
    assert codes(findings) == ["DVR-GUI-SCAN-FAILED"]


def test_real_process_scan_finds_this_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = psutil.Process().name()
    config = make_config(tmp_path, davinci={"gui_process_names": [name]})
    findings = davinci_gui_findings(config)
    assert any(f"(pid {psutil.Process().pid})" in f.message for f in findings)
