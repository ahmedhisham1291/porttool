"""The adapters: template in, fake tool run, ToolResult out."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from porttool.core.config import ToolTimeouts
from porttool.core.errors import LicenseError, ToolTimeoutError
from porttool.core.interfaces import DaVinciRunner, ToolResult
from porttool.core.results import Severity
from porttool.davinci import DaVinciToolRunner, write_script_request
from porttool.davinci.runner import timeout_for
from tests.fakes import FAKE_SCRIPT
from tests.unit.davinci.helpers import (
    FakeCtx,
    codes,
    fake_commands,
    fake_vars,
    make_config,
    run,
)

_CONFIG_OPTIONS = frozenset(
    {
        "davinci",
        "commands",
        "timeouts",
        "pipeline",
        "generate_modules",
        "sip_root",
        "developer_bin",
        "dcf_file",
        "dpa_file",
    }
)


def _runner(tmp_path: Path, **options: Any) -> DaVinciToolRunner:
    """A runner on the fake tools. Options that are not config options drive the fake."""
    fake = {key: options.pop(key) for key in list(options) if key not in _CONFIG_OPTIONS}
    fake.setdefault("record_file", tmp_path / "record.jsonl")
    return DaVinciToolRunner(make_config(tmp_path, env=fake_vars(**fake), **options))


def _records(tmp_path: Path) -> list[dict[str, Any]]:
    path = tmp_path / "record.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _errors(result: ToolResult) -> list[str]:
    return [f.code for f in result.findings if f.severity is Severity.ERROR]


def test_runner_implements_the_contract(tmp_path: Path) -> None:
    runner: DaVinciRunner = _runner(tmp_path)
    assert runner is not None


def test_check_workspace_runs_the_template_in_the_repo_root(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    result = run(runner.check_workspace(FakeCtx()))
    assert result == ToolResult("DVWspChecker", True, 0, result.elapsed_s, result.log_file, ())
    (record,) = _records(tmp_path)
    repo = runner.config.repo_root
    assert record["tool"] == "DVWspChecker"
    assert record["args"] == ["-w", str(repo / "Workspace.dcf")]
    assert record["cwd"] == str(repo)
    assert result.log_file is not None
    log = Path(result.log_file)
    assert log.parent == tmp_path / "logs" and log.name.endswith("_check_DVWspChecker.log")
    text = log.read_text(encoding="utf-8")
    assert "tool: DVWspChecker; action: check; command template: set in porttool.toml" in text
    outcome = runner.last_outcome("check")
    assert outcome is not None and outcome.tail == (
        "[fake] DVWspChecker started with 2 argument(s)",
    )


def test_import_arxml_passes_the_file(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    arxml = tmp_path / "delta.arxml"
    arxml.write_text("<AUTOSAR/>", encoding="utf-8")
    result = run(runner.import_arxml(arxml, FakeCtx()))
    assert result.ok and result.tool == "DVImEx"
    assert _records(tmp_path)[0]["args"][-2:] == ["-i", str(arxml)]


def test_import_arxml_that_does_not_exist_starts_nothing(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    result = run(runner.import_arxml(tmp_path / "missing.arxml", FakeCtx()))
    assert not result.ok and result.exit_code is None and result.log_file is None
    assert _errors(result) == ["DVR-PATH-MISSING"]
    assert _records(tmp_path) == []


def test_generate_passes_the_modules(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    result = run(runner.generate(["Rte", "Os"], FakeCtx()))
    assert result.ok and result.tool == "DVCfgCmd"
    assert _records(tmp_path)[0]["args"][-3:] == ["-g", "-m", "Rte,Os"]


def test_generate_without_modules_uses_the_configured_ones(tmp_path: Path) -> None:
    runner = _runner(tmp_path, generate_modules=("Rte",))
    assert run(runner.generate([], FakeCtx())).ok
    assert _records(tmp_path)[0]["args"][-1] == "Rte"


def test_generate_with_no_module_at_all_starts_nothing(tmp_path: Path) -> None:
    runner = _runner(tmp_path, generate_modules=())
    result = run(runner.generate([], FakeCtx()))
    assert _errors(result) == ["DVR-ARG-EMPTY"] and _records(tmp_path) == []
    result = run(runner.generate_contract_headers([], FakeCtx()))
    assert _errors(result) == ["DVR-ARG-EMPTY"] and result.tool == "DVSwcGen"


def test_contract_headers_expand_the_swc_list(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    result = run(runner.generate_contract_headers(["SwcA", "SwcB"], FakeCtx()))
    assert result.ok and result.tool == "DVSwcGen"
    assert _records(tmp_path)[0]["args"][-3:] == ["-c", "SwcA", "SwcB"]


def test_validate_project_runs_dvcfgcmd(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    result = run(runner.validate_project(FakeCtx()))
    assert result.ok
    assert _records(tmp_path)[0]["args"] == ["-p", str(runner.config.dpa_file), "--validate"]


def test_script_task_gets_paths_as_arguments_and_environment(tmp_path: Path) -> None:
    show = "import os; print(os.environ['PORTTOOL_REQUEST_JSON']); "
    show += "print(os.environ['PORTTOOL_RESULT_JSON']); print(os.environ['PORTTOOL_LOG_DIR'])"
    commands = {**fake_commands(), "script_task": [sys.executable, "-c", show, "{script_task}"]}
    runner = _runner(tmp_path, commands=commands)
    request = tmp_path / "request.json"
    write_script_request([], request)
    result_file = tmp_path / "result.json"
    result = run(runner.run_script_task(request, result_file, FakeCtx()))
    assert result.exit_code == 0
    outcome = runner.last_outcome("script_task")
    assert outcome is not None
    assert outcome.tail == (str(request), str(result_file), str(tmp_path / "logs"))
    assert outcome.argv[-1] == "PortToolMapEvents"


def test_exit_code_zero_with_error_lines_is_not_ok(tmp_path: Path) -> None:
    runner = _runner(tmp_path, stdout=["start", "ERROR: port missing", "done"], exit_code=0)
    result = run(runner.check_workspace(FakeCtx()))
    assert result.exit_code == 0 and not result.ok
    (finding,) = result.findings
    assert finding.code == "DVR-RUN-ERROR-LINE" and finding.severity is Severity.ERROR
    assert finding.file == result.log_file
    assert "ERROR: port missing" in finding.message
    line_no = int(finding.message.split("line ")[1].split(" ")[0])
    assert result.log_file is not None
    log_lines = Path(result.log_file).read_text(encoding="utf-8").splitlines()
    assert log_lines[line_no - 1] == "[out] ERROR: port missing"


def test_non_zero_exit_with_only_warnings_and_that_code_listed_as_success(tmp_path: Path) -> None:
    davinci = {"results": {"success_exit_codes": {"generate": [0, 2]}}}
    runner = _runner(tmp_path, davinci=davinci, stdout=["Warning: slow"], exit_code=2)
    result = run(runner.generate(["Rte"], FakeCtx()))
    assert result.ok and result.exit_code == 2
    assert codes(result.findings) == ["DVR-RUN-WARNING-LINE"]
    assert result.findings[0].severity is Severity.WARNING
    # The same code is a failure for an action that does not list it.
    other = run(runner.validate_project(FakeCtx()))
    assert not other.ok and "DVR-RUN-EXIT" in _errors(other)


def test_exit_code_that_is_not_allowed_says_which_line_to_add(tmp_path: Path) -> None:
    runner = _runner(tmp_path, exit_code=3)
    result = run(runner.check_workspace(FakeCtx()))
    assert not result.ok and result.exit_code == 3
    (finding,) = result.findings
    assert finding.code == "DVR-RUN-EXIT"
    assert "[davinci.results.success_exit_codes], add the line: check = [0, 3]" in finding.message


def test_crash_exit_code_is_reported_as_a_failure(tmp_path: Path) -> None:
    runner = _runner(tmp_path, crash=True, crash_code=134)
    result = run(runner.generate(["Rte"], FakeCtx()))
    assert not result.ok and result.exit_code == 134
    assert "DVR-RUN-EXIT" in _errors(result)


def test_windows_crash_code_is_shown_in_hex(tmp_path: Path) -> None:
    from porttool.davinci.procrun import ProcessOutcome

    runner = _runner(tmp_path)
    outcome = ProcessOutcome(("x",), 0xC0000005, 1.0, tmp_path / "x.log")
    result = runner._judge("generate", "DVCfgCmd", outcome, [], [])
    assert "exit code 3221225477 (0xC0000005)" in result.findings[0].message
    assert "usually means the tool crashed" in result.findings[0].message


def test_more_than_twenty_error_lines_are_summarised(tmp_path: Path) -> None:
    runner = _runner(tmp_path, stdout=[f"error {n}" for n in range(25)])
    result = run(runner.check_workspace(FakeCtx()))
    assert len(result.findings) == 21
    assert "5 more error line(s)" in result.findings[-1].message


def test_license_problem_raises_through_the_adapter(tmp_path: Path) -> None:
    runner = _runner(tmp_path, license_error="License server could not be reached")
    with pytest.raises(LicenseError, match="DVCfgCmd \\(action generate\\)"):
        run(runner.generate(["Rte"], FakeCtx()))


def test_timeout_comes_from_the_project_timeouts(tmp_path: Path) -> None:
    timeouts = ToolTimeouts(workspace_check_s=0.5)
    runner = _runner(tmp_path, timeouts=timeouts, hang=True)
    with pytest.raises(ToolTimeoutError, match="DVWspChecker \\(action check\\)"):
        run(runner.check_workspace(FakeCtx()))
    assert timeout_for("generate", timeouts) == timeouts.generate_s
    assert timeout_for("contract_headers", timeouts) == timeouts.generate_s
    assert timeout_for("import", timeouts) == timeouts.import_s
    assert timeout_for("script_task", timeouts) == timeouts.script_task_s
    assert timeout_for("validate", timeouts) == timeouts.validate_s


def test_report_file_written_by_the_run_is_scanned(tmp_path: Path) -> None:
    report = tmp_path / "repo" / "Log" / "check_report.txt"
    (tmp_path / "old").mkdir()
    davinci = {"results": {"report_globs": ["Log/*.txt", "{log_dir}/*.rpt"]}}
    runner = _runner(
        tmp_path,
        davinci=davinci,
        report_file=report,
        report_text="ok\nError 17: dangling reference\nwarning: unused\n",
    )
    report.parent.mkdir()
    stale = report.parent / "old_report.txt"
    stale.write_text("Error from last week\n", encoding="utf-8")
    os.utime(stale, (1_000_000, 1_000_000))
    result = run(runner.check_workspace(FakeCtx()))
    assert not result.ok and result.exit_code == 0
    assert codes(result.findings) == ["DVR-RUN-ERROR-LINE", "DVR-RUN-WARNING-LINE"]
    assert result.findings[0].file == str(report)
    assert "line 2 of the report: Error 17: dangling reference" in result.findings[0].message


def test_license_problem_in_a_report_file_raises(tmp_path: Path) -> None:
    report = tmp_path / "repo" / "report.txt"
    davinci = {"results": {"report_globs": [str(report)]}}
    runner = _runner(tmp_path, davinci=davinci, report_file=report, report_text="license expired\n")
    with pytest.raises(LicenseError, match="report"):
        run(runner.check_workspace(FakeCtx()))


def test_child_that_outlives_the_tool_is_a_warning_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from porttool.davinci import procrun

    monkeypatch.setattr(procrun, "LEFTOVER_GRACE_S", 0.3)
    pid_file = tmp_path / "child.pid"
    runner = _runner(tmp_path, spawn_child_s=60, child_pid_file=pid_file, delay_s=0.6)
    result = run(runner.check_workspace(FakeCtx()))
    assert result.ok
    (finding,) = result.findings
    assert finding.code == "DVR-RUN-ORPHAN" and finding.severity is Severity.WARNING
    assert f"(pid {pid_file.read_text(encoding='utf-8')})" in finding.message
    assert "They were stopped." in finding.message


def test_old_tool_logs_are_pruned(tmp_path: Path) -> None:
    runner = _runner(tmp_path, davinci={"max_tool_logs": 3})
    for _ in range(5):
        assert run(runner.check_workspace(FakeCtx())).ok
    assert len(list((tmp_path / "logs").glob("*.log"))) == 3


def test_log_folder_that_cannot_be_created_is_a_finding(tmp_path: Path) -> None:
    blocker = tmp_path / "blocked"
    blocker.write_text("file", encoding="utf-8")
    runner = _runner(tmp_path, davinci={"log_dir": str(blocker / "logs")})
    result = run(runner.check_workspace(FakeCtx()))
    assert _errors(result) == ["DVR-LOG-DIR"] and _records(tmp_path) == []
    assert "log_dir = " in result.findings[0].message


def test_default_log_folder_is_below_the_porttool_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from porttool.core.config import ProjectConfig
    from porttool.davinci import runner as runner_module

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(runner_module, "current_log_file", lambda: None)
    config = ProjectConfig(repo_root=tmp_path)
    runner = DaVinciToolRunner(config)
    assert runner.log_dir() == tmp_path / "appdata" / "PortTool" / "logs" / "tools"
    # With --log-dir the tool logs follow the application log.
    monkeypatch.setattr(runner_module, "current_log_file", lambda: tmp_path / "x" / "app.log")
    assert runner.log_dir() == tmp_path / "x" / "tools"
    assert DaVinciToolRunner(config, log_dir=tmp_path).log_dir() == tmp_path


def test_builtin_templates_run_a_found_executable_and_say_unverified(tmp_path: Path) -> None:
    bin_dir = tmp_path / "Vector Tools" / "DaVinciConfigurator" / "Core"
    bin_dir.mkdir(parents=True)
    exe = bin_dir / "DVCfgCmd.exe"
    shutil.copy(FAKE_SCRIPT, exe)  # the fake has a shebang line and names itself by file name
    exe.chmod(0o755)
    config = make_config(
        tmp_path,
        commands={},
        env=fake_vars(record_file=tmp_path / "record.jsonl"),
        sip_root=tmp_path / "Vector Tools",
    )
    runner = DaVinciToolRunner(config)
    result = run(runner.generate(["Rte", "Os"], FakeCtx()))
    assert result.ok and result.exit_code == 0
    (finding,) = result.findings
    assert finding.code == "DVR-TPL-UNVERIFIED" and finding.severity is Severity.WARNING
    assert "UNVERIFIED" in finding.message
    record = _records(tmp_path)[0]
    assert record["args"] == ["-p", str(config.dpa_file), "-g", "-m", "Rte,Os"]
