"""Step 10: finding the Vector tools and capturing their help output with fake tools."""

from __future__ import annotations

import struct
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from porttool.discovery import proc, tools
from porttool.discovery.context import Context
from porttool.discovery.fsio import FileEntry
from porttool.discovery.proc import decode_output, hidden_window_options, run_captured
from porttool.discovery.tools import (
    HELP_FLAGS,
    ToolSearch,
    build_tools,
    find_executables,
    help_command,
    parse_pe_version,
)
from tests.unit.discovery import synth
from tests.unit.discovery.conftest import Runner, codes

PY = (".py",)


def _fake(folder: Path, name: str, body: str) -> Path:
    return synth.write(folder, name + ".py", body)


def _with_tools(**extra: object) -> dict[str, object]:
    return {
        "no_tools": False,
        "tool_suffixes": PY,
        "tool_launcher": synth.launcher,
        "tool_timeout_s": 20.0,
        "environ": {},
        **extra,
    }


def _alive(pid: int) -> bool:
    if not psutil.pid_exists(pid):
        return False
    return bool(psutil.Process(pid).status() != psutil.STATUS_ZOMBIE)


# ---- safety rule


def test_a_tool_is_only_ever_started_with_one_help_flag() -> None:
    assert HELP_FLAGS == ("--help", "-h", "-?", "/?", "help")
    assert help_command(["tool.exe"], "/?") == ["tool.exe", "/?"]
    for forbidden in ("", "--project", "C:\\repo\\ECU.dpa", "-p", "--help ECU.dcf"):
        with pytest.raises(ValueError, match="refusing"):
            help_command(["tool.exe"], forbidden)


def test_no_project_or_workspace_path_is_ever_passed_to_a_tool(
    git_repo: Path, tmp_path: Path, discover: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in tools.ALL_TOOLS:
        _fake(tmp_path / "bin", name, synth.FAKE_GOOD)
    seen: list[tuple[list[str], str | None]] = []

    def spy(argv, **kwargs):  # type: ignore[no-untyped-def]
        seen.append((list(argv), kwargs.get("cwd")))
        return run_captured(argv, **kwargs)

    monkeypatch.setattr("porttool.discovery.tools.run_captured", spy)
    discover(
        git_repo,
        **_with_tools(
            developer_bin=str(tmp_path / "bin"), dpa=str(git_repo / "Config/EcuProj.dpa")
        ),
    )
    assert len(seen) == 4 * len(HELP_FLAGS)
    for argv, cwd in seen:
        assert len(argv) == 3 and argv[0] == sys.executable and argv[2] in HELP_FLAGS
        assert "repo" not in argv[2] and ".dpa" not in argv[2] and ".dcf" not in argv[2]
        assert cwd is not None and str(git_repo) not in cwd  # never the repo as working folder
        assert not Path(cwd).exists()  # the temporary working folder is removed again


# ---- corner cases


def test_tool_not_found_is_a_finding_for_each_missing_tool(
    git_repo: Path, discover: Runner
) -> None:
    result = discover(git_repo, **_with_tools())
    section = result.report["tools"]
    assert section["skipped"] is False and section["executables"] == []
    assert section["not_found"] == ["DVImEx", "DVWspChecker", "DVSwcGen", "DVCfgCmd"]
    assert codes(result).count("DISC-TOOL-NOT-FOUND") == 4
    assert all(step["ok"] for step in result.report["steps"])


def test_help_output_of_each_flag_is_saved_with_exit_code(
    git_repo: Path, tmp_path: Path, discover: Runner
) -> None:
    _fake(tmp_path / "bin", "DVImEx", synth.FAKE_GOOD)
    result = discover(git_repo, **_with_tools(developer_bin=str(tmp_path / "bin")))
    item = result.report["tools"]["executables"][0]
    assert item["tool"] == "DVImEx" and item["found_via"] == "--developer-bin"
    assert item["bytes"] > 0 and item["modified_utc"].endswith("Z")
    assert [a["flag"] for a in item["attempts"]] == list(HELP_FLAGS)
    assert all(a["exit_code"] == 0 and a["looks_like_help"] for a in item["attempts"])
    cli = Path(result.out_dir) / "cli"
    assert sorted(path.name for path in cli.iterdir()) == [
        "DVImEx_dash-h.txt",
        "DVImEx_dash-question.txt",
        "DVImEx_dashdash-help.txt",
        "DVImEx_help.txt",
        "DVImEx_slash-question.txt",
    ]
    text = (cli / "DVImEx_slash-question.txt").read_text(encoding="utf-8")
    assert "# argument: /?" in text and "# result: exit code 0" in text
    assert "Usage: tool [options]" in text and "argv=['/?']" in text


def test_tool_that_returns_non_zero_for_help_is_recorded_not_failed(
    git_repo: Path, tmp_path: Path, discover: Runner
) -> None:
    _fake(tmp_path / "bin", "DVWspChecker", synth.FAKE_NONZERO)
    result = discover(git_repo, **_with_tools(developer_bin=str(tmp_path / "bin")))
    attempts = result.report["tools"]["executables"][0]["attempts"]
    assert all(a["exit_code"] == 3 and a["stderr_bytes"] > 0 for a in attempts)
    assert codes(result).count("DISC-TOOL-EXIT") == 5
    text = (Path(result.out_dir) / "cli" / "DVWspChecker_dash-h.txt").read_text(encoding="utf-8")
    assert "# result: exit code 3" in text and "Unknown option" in text


def test_tool_that_prints_non_utf8_bytes_is_saved_without_losing_bytes(
    git_repo: Path, tmp_path: Path, discover: Runner
) -> None:
    _fake(tmp_path / "bin", "DVSwcGen", synth.FAKE_BINARY)
    result = discover(git_repo, **_with_tools(developer_bin=str(tmp_path / "bin")))
    attempt = result.report["tools"]["executables"][0]["attempts"][0]
    assert attempt["decoding"] == "utf-8 with \\x escapes"
    text = (Path(result.out_dir) / "cli" / "DVSwcGen_dashdash-help.txt").read_text(encoding="utf-8")
    assert "Usage \\xff\\xfa caf\\xe9" in text
    assert decode_output(b"\xff\xfeh\x00i\x00") == ("hi", "utf-16")
    assert decode_output(b"") == ("", "empty")
    assert decode_output("grün".encode()) == ("grün", "utf-8")


def test_hanging_tool_is_killed_on_timeout_together_with_its_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _fake(tmp_path, "DVCfgCmd", synth.FAKE_HANG)
    pid_file = tmp_path / "pids.txt"
    monkeypatch.setenv("FAKE_PID_FILE", str(pid_file))
    started = time.monotonic()
    result = run_captured([sys.executable, str(script), "--help"], timeout_s=3.0)
    elapsed = time.monotonic() - started
    assert result.timed_out is True and result.exit_code is None
    assert elapsed < 12.0
    parent, child = (int(part) for part in pid_file.read_text(encoding="ascii").split())
    assert result.killed == 2 and result.survivors == 0
    assert not _alive(parent) and not _alive(child)
    assert result.stdout.strip() == b"started"  # output before the kill is kept


def test_hanging_tool_in_a_full_run_gives_findings_and_the_report_is_still_written(
    git_repo: Path, tmp_path: Path, discover: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake(tmp_path / "sip" / "DaVinciConfigurator" / "Core", "DVCfgCmd", synth.FAKE_HANG)
    pid_file = tmp_path / "pids.txt"
    monkeypatch.setenv("FAKE_PID_FILE", str(pid_file))
    result = discover(git_repo, **_with_tools(sip=str(tmp_path / "sip"), tool_timeout_s=1.5,
                                              environ=None))  # fmt: skip
    item = result.report["tools"]["executables"][0]
    assert item["found_via"] == "--sip"
    assert [a["timed_out"] for a in item["attempts"]] == [True] * 5
    assert all(a["processes_still_alive"] == 0 for a in item["attempts"])
    assert codes(result).count("DISC-TOOL-TIMEOUT") == 5
    pids = [int(part) for part in pid_file.read_text(encoding="ascii").split()]
    assert len(pids) == 10 and not any(_alive(pid) for pid in pids)
    text = (Path(result.out_dir) / "cli" / "DVCfgCmd_help.txt").read_text(encoding="utf-8")
    assert "no exit within" in text and "killed 2 process(es)" in text
    assert (Path(result.out_dir) / "discovery.json").is_file()


def test_tool_that_cannot_be_started_is_a_finding(git_repo: Path, tmp_path: Path) -> None:
    _fake(tmp_path / "bin", "DVImEx", synth.FAKE_GOOD)
    ctx = Context()
    search = ToolSearch(developer_bin=str(tmp_path / "bin"), suffixes=PY, environ={})
    result = build_tools(
        search, ctx, repo_root=str(git_repo), launcher=lambda path: ["no-such-program-xyz", path]
    )
    attempts = result.section["executables"][0]["attempts"]
    assert all(a["spawn_error"] == "FileNotFoundError" for a in attempts)
    assert [f.code for _, f in ctx.findings].count("DISC-TOOL-SPAWN") == 5
    missing = run_captured(["no-such-program-xyz"], timeout_s=5)
    assert missing.started is False and missing.ok is False


# ---- search order


def test_search_covers_developer_bin_program_files_sip_dpa_paths_and_repo(
    repo: Path, tmp_path: Path, discover: Runner
) -> None:
    _fake(tmp_path / "bin" / "sub", "DVImEx", synth.FAKE_GOOD)
    program_files = tmp_path / "Program Files (x86)"
    _fake(program_files / "Vector DaVinci Developer 4.12" / "Bin", "DVWspChecker", synth.FAKE_GOOD)
    _fake(program_files / "Some Other Vendor" / "Bin", "DVSwcGen", synth.FAKE_GOOD)
    _fake(repo / "Sip" / "DaVinciConfigurator" / "Core", "DVCfgCmd", synth.FAKE_GOOD)
    result = discover(
        repo,
        **_with_tools(
            developer_bin=str(tmp_path / "bin"),
            environ={"ProgramFiles(x86)": str(program_files), "ProgramFiles": str(tmp_path / "no")},
        ),
    )
    found = {item["tool"]: item for item in result.report["tools"]["executables"]}
    assert found["DVImEx"]["found_via"] == "--developer-bin"
    assert found["DVWspChecker"]["found_via"] == "Program Files"
    assert found["DVCfgCmd"]["found_via"] == "path in the .dpa file"  # <SIP>..\Sip</SIP>
    assert found["DVCfgCmd"]["path"] == "<repo>/Sip/DaVinciConfigurator/Core/DVCfgCmd.py"
    assert result.report["tools"]["not_found"] == ["DVSwcGen"]
    assert len(result.report["tools"]["search"]["developer_folders_found"]) == 1


def test_tool_inside_the_repo_is_found_without_any_option(repo: Path, tmp_path: Path) -> None:
    (repo / "Config" / "EcuProj.dpa").unlink()
    path = _fake(repo / "Tools" / "DaVinciConfigurator" / "Core", "dvcfgcmd", synth.FAKE_GOOD)
    entry = FileEntry("Tools/DaVinciConfigurator/Core/dvcfgcmd.py", str(path), 1, 0)
    found = find_executables(ToolSearch(repo_files=[entry], suffixes=PY, environ={}))
    assert [(item.tool, item.found_via) for item in found] == [("DVCfgCmd", "inside the repo")]


def test_folder_given_with_option_that_does_not_exist_is_a_finding(
    git_repo: Path, tmp_path: Path, discover: Runner
) -> None:
    result = discover(git_repo, **_with_tools(sip=str(tmp_path / "missing_sip")))
    assert "DISC-TOOL-FOLDER" in codes(result)


def test_only_the_first_three_copies_of_a_tool_are_started(git_repo: Path, tmp_path: Path) -> None:
    for number in range(4):
        _fake(tmp_path / "bin" / f"v{number}", "DVImEx", synth.FAKE_GOOD)
    ctx = Context()
    search = ToolSearch(developer_bin=str(tmp_path / "bin"), suffixes=PY, environ={})
    result = build_tools(search, ctx, repo_root=str(git_repo), launcher=synth.launcher)
    listed = result.section["executables"]
    assert [len(item["attempts"]) for item in listed] == [5, 5, 5, 0]
    assert "DVImEx-2_help.txt" in result.cli_files and "DVImEx-4_help.txt" not in result.cli_files


# ---- file version and window options (logic only; real behaviour is Windows-only)


def _version_resource() -> bytes:
    fixed = struct.pack("<6I", 0xFEEF04BD, 0x00010000, (4 << 16) | 12, (30 << 16) | 7,
                        (4 << 16) | 12, 0)  # fmt: skip

    def entry(key: str, value: str) -> bytes:
        return key.encode("utf-16-le") + b"\x00\x00\x00\x00" + value.encode("utf-16-le") + b"\0\0"

    return (
        b"MZ" + b"\x00" * 100 + fixed + b"\x00" * 28
        + entry("CompanyName", "Example Vendor GmbH")
        + entry("FileVersion", "4.12.30.7 (build)")
        + entry("ProductName", "Example Tool")
    )  # fmt: skip


def test_file_version_is_read_from_the_version_resource_bytes(tmp_path: Path) -> None:
    version = parse_pe_version(_version_resource())
    assert version is not None
    assert version.file_version == "4.12.30.7" and version.product_version == "4.12.0.0"
    assert version.strings == {
        "CompanyName": "Example Vendor GmbH",
        "ProductName": "Example Tool",
        "FileVersion": "4.12.30.7 (build)",
    }
    assert parse_pe_version(b"MZ no version here") is None
    assert parse_pe_version(b"\xbd\x04\xef\xfe" + b"\x01" * 30) is None
    exe = tmp_path / "tool.exe"
    exe.write_bytes(_version_resource())
    read = tools.read_file_version(str(exe))
    assert read is not None and read.file_version == "4.12.30.7"


def test_hidden_window_options_use_the_windows_flags_when_they_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert hidden_window_options() == {}  # this sandbox is not Windows: no such names

    class FakeStartupInfo:
        dwFlags = 0
        wShowWindow = 5

    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(subprocess, "STARTUPINFO", FakeStartupInfo, raising=False)
    monkeypatch.setattr(subprocess, "STARTF_USESHOWWINDOW", 1, raising=False)
    monkeypatch.setattr(subprocess, "SW_HIDE", 0, raising=False)
    options = hidden_window_options()
    assert options["creationflags"] == 0x08000000
    assert options["startupinfo"].dwFlags == 1 and options["startupinfo"].wShowWindow == 0


def test_kill_process_tree_of_a_process_that_is_already_gone() -> None:
    done = subprocess.Popen([sys.executable, "-c", "pass"])  # noqa: S603
    done.wait()
    assert proc.kill_process_tree(done.pid) == (0, 0)


def test_output_longer_than_the_limit_is_cut_and_flagged() -> None:
    result = run_captured([sys.executable, "-c", "print('x' * 5000)"], timeout_s=20, max_bytes=100)
    assert result.ok and result.truncated and len(result.stdout) == 100


@pytest.mark.windows_only
@pytest.mark.skipif(sys.platform != "win32", reason="needs a real Windows desktop")
def test_real_windows_hidden_window_file_version_and_tree_kill() -> None:
    """Placeholder for the checks that only a Windows machine can make (see HANDOFF.md)."""
    assert "creationflags" in hidden_window_options()
