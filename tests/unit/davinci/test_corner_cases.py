"""One named test per corner case of the task, at the level a caller sees: the adapters
of ``DaVinciToolRunner``, with the fake tools started through command templates.

The same cases are also tested closer to the code in test_procrun.py, test_settings.py,
test_script.py and test_doctor.py.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import time
import tracemalloc
from pathlib import Path
from typing import Any

import psutil
import pytest

from porttool.core.config import ProjectConfig, ToolTimeouts
from porttool.core.errors import JobCancelled, LicenseError, ToolTimeoutError
from porttool.core.interfaces import JobContext, ToolResult
from porttool.core.results import Severity
from porttool.davinci import (
    DaVinciToolRunner,
    ScriptEvent,
    davinci_gui_findings,
    procrun,
    read_script_result,
    write_script_request,
)
from porttool.davinci.procrun import is_alive
from porttool.engine import Engine
from tests.fakes import FAKE_SCRIPT, cmd_wrapper
from tests.unit.davinci.helpers import (
    WAIT,
    FakeCtx,
    codes,
    fake_commands,
    fake_template,
    fake_vars,
    make_config,
    run,
)

_CONFIG_OPTIONS = frozenset(
    {"davinci", "commands", "timeouts", "generate_modules", "sip_root", "developer_bin"}
    | {"dcf_file", "dpa_file"}
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


def _alive(pid: int) -> bool:
    try:
        return is_alive(psutil.Process(pid))
    except psutil.NoSuchProcess:
        return False


def _dead_soon(pid: int) -> bool:
    deadline = time.monotonic() + 5.0
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    return not _alive(pid)


def _not_started(result: ToolResult, tmp_path: Path) -> bool:
    return (
        not result.ok
        and result.exit_code is None
        and result.log_file is None
        and _records(tmp_path) == []
    )


@pytest.fixture(autouse=True)
def _short_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(procrun, "LEFTOVER_GRACE_S", 0.3)
    monkeypatch.setattr(procrun, "PIPE_DRAIN_S", 1.0)


# ------------------------------------------------------------------ before a tool starts


def test_executable_missing(tmp_path: Path) -> None:
    empty = tmp_path / "empty bin"
    empty.mkdir()
    runner = _runner(tmp_path, commands={"check": ["{exe}", "-w", "{dcf}"]}, developer_bin=empty)
    result = run(runner.check_workspace(FakeCtx()))
    assert _not_started(result, tmp_path)
    (finding,) = result.findings
    assert finding.code == "DVR-EXE-MISSING" and finding.severity is Severity.ERROR
    assert f"Looked for: {empty / 'DaVinciConfigurator' / 'Core' / 'DVWspChecker.exe'}" in (
        finding.message
    )
    assert "table [davinci.executables], add the line: dvwspchecker = " in finding.message


def test_executable_missing_when_the_configured_path_is_wrong(tmp_path: Path) -> None:
    davinci = {"executables": {"dvwspchecker": str(tmp_path / "gone" / "DVWspChecker.exe")}}
    runner = _runner(tmp_path, davinci=davinci, commands={"check": ["{exe}", "{dcf}"]})
    result = run(runner.check_workspace(FakeCtx()))
    assert _not_started(result, tmp_path)
    assert codes(result.findings) == ["DVR-EXE-MISSING"]
    assert "names a file that does not exist" in result.findings[0].message


def test_executable_missing_when_the_template_names_the_program_itself(tmp_path: Path) -> None:
    runner = _runner(tmp_path, commands={"check": [str(tmp_path / "nothing.exe"), "{dcf}"]})
    result = run(runner.check_workspace(FakeCtx()))
    assert _not_started(result, tmp_path)
    assert codes(result.findings) == ["DVR-EXE-MISSING"]
    assert "[davinci.commands] check" in result.findings[0].message


def test_path_with_spaces_and_non_ascii_characters(tmp_path: Path) -> None:
    tools = tmp_path / "Vector Tools äöü 日本"
    tools.mkdir()
    exe = tools / "DVWspChecker.exe"
    shutil.copy(FAKE_SCRIPT, exe)  # the fake has a shebang line and names itself by file name
    exe.chmod(0o755)
    repo = tmp_path / "Mein ECU Größe"
    repo.mkdir()
    dcf = repo / "Arbeits bereich ß.dcf"
    dcf.write_text("dcf", encoding="utf-8")
    config = ProjectConfig(
        repo_root=repo,
        dcf_file=dcf,
        extras={
            "davinci": {
                "log_dir": str(tmp_path / "Logs mit Ümlaut"),
                "executables": {"dvwspchecker": str(exe)},
                "commands": {"check": ["{exe}", "-w", "{dcf}", "--out={log_dir}/a b.txt"]},
                "env": fake_vars(record_file=tmp_path / "record.jsonl"),
            }
        },
    )
    result = run(DaVinciToolRunner(config).check_workspace(FakeCtx()))
    assert result.ok and result.exit_code == 0 and result.findings == ()
    (record,) = _records(tmp_path)
    assert record["args"] == ["-w", str(dcf), f"--out={tmp_path / 'Logs mit Ümlaut'}/a b.txt"]
    assert record["cwd"] == str(repo)
    assert result.log_file is not None and "Logs mit Ümlaut" in result.log_file
    assert str(exe) in Path(result.log_file).read_text(encoding="utf-8")


def test_unresolved_placeholder_starts_nothing(tmp_path: Path) -> None:
    # {arxml} has no value in the check action; {modules} has none in validate.
    commands = {**fake_commands(), "check": fake_template("DVWspChecker", "{dcf}", "{arxml}")}
    commands["validate"] = fake_template("DVCfgCmd", "{modules}")
    runner = _runner(tmp_path, commands=commands)
    for result in (run(runner.check_workspace(FakeCtx())), run(runner.validate_project(FakeCtx()))):
        assert _not_started(result, tmp_path)
        assert codes(result.findings) == ["DVR-TPL-UNRESOLVED"]
        assert "The tool was not started." in result.findings[0].message


def test_unknown_placeholder_starts_nothing(tmp_path: Path) -> None:
    commands = {**fake_commands(), "check": fake_template("DVWspChecker", "{workspace}")}
    result = run(_runner(tmp_path, commands=commands).check_workspace(FakeCtx()))
    assert _not_started(result, tmp_path)
    assert codes(result.findings) == ["DVR-TPL-UNKNOWN"]


def test_template_that_is_not_a_list_starts_nothing(tmp_path: Path) -> None:
    commands: dict[str, Any] = {**fake_commands(), "generate": "DVCfgCmd.exe -p {dpa} -g"}
    runner = _runner(tmp_path, commands=commands)
    result = run(runner.generate(["Rte"], FakeCtx()))
    assert _not_started(result, tmp_path)
    assert codes(result.findings) == ["DVR-TPL-NOT-LIST"]
    assert "must be a list of arguments" in result.findings[0].message
    # The other actions are not affected by the broken one.
    assert run(runner.check_workspace(FakeCtx())).ok


def test_dcf_missing(tmp_path: Path) -> None:
    not_set = _runner(tmp_path, dcf_file=None)
    result = run(not_set.check_workspace(FakeCtx()))
    assert _not_started(result, tmp_path)
    assert codes(result.findings) == ["DVR-TPL-UNRESOLVED"]
    assert "set dcf_file in the [paths] table of porttool.toml" in result.findings[0].message

    gone = _runner(tmp_path, dcf_file=tmp_path / "repo" / "Deleted.dcf")
    result = run(gone.check_workspace(FakeCtx()))
    assert _not_started(result, tmp_path)
    assert codes(result.findings) == ["DVR-PATH-MISSING"]
    assert "Deleted.dcf" in result.findings[0].message
    assert "DVWspChecker was not started" in result.findings[0].message


def test_dpa_missing(tmp_path: Path) -> None:
    not_set = _runner(tmp_path, dpa_file=None)
    result = run(not_set.generate(["Rte"], FakeCtx()))
    assert _not_started(result, tmp_path)
    assert codes(result.findings) == ["DVR-TPL-UNRESOLVED"]
    assert "set dpa_file in the [paths] table of porttool.toml" in result.findings[0].message

    gone = _runner(tmp_path, dpa_file=tmp_path / "repo" / "Deleted.dpa")
    for result in (run(gone.validate_project(FakeCtx())), run(gone.generate(["Rte"], FakeCtx()))):
        assert _not_started(result, tmp_path)
        assert codes(result.findings) == ["DVR-PATH-MISSING"]


# ------------------------------------------------------------------ license


@pytest.mark.parametrize(
    "text",
    [
        "ERROR: License for DaVinci Configurator not found",
        "Your licence has expired on 2026-10-31",
        "Could not reach the license server lic01:7000 (server unreachable)",
    ],
    ids=["missing", "expired", "server-unreachable"],
)
def test_license_missing_expired_or_server_unreachable(tmp_path: Path, text: str) -> None:
    runner = _runner(tmp_path, license_error=text, exit_code=0)
    with pytest.raises(LicenseError) as caught:
        run(runner.validate_project(FakeCtx()))
    message = str(caught.value)
    assert message.startswith("[PT-E620] DVCfgCmd (action validate) reported a license problem")
    assert text in message and "Check the Vector license" in message
    log = Path(caught.value.detail or "")
    assert log.is_file() and text in log.read_text(encoding="utf-8")


# ------------------------------------------------------------------ while a tool runs


def test_tool_hangs_with_no_output_timeout_tree_killed_child_killed_too(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    runner = _runner(
        tmp_path,
        timeouts=ToolTimeouts(generate_s=1.0),
        hang=True,
        stdout=[],
        spawn_child_s=120,
        child_pid_file=pid_file,
        # The child keeps the output pipes open, as a real helper process would.
        child_inherits_pipes=True,
    )
    started = time.monotonic()
    with pytest.raises(ToolTimeoutError) as caught:
        run(runner.generate(["Rte"], FakeCtx()))
    assert time.monotonic() - started < 15
    assert "DVCfgCmd (action generate) did not finish within 1 s" in str(caught.value)
    assert "2 process(es) killed" in str(caught.value)
    (record,) = _records(tmp_path)
    assert not _alive(record["pid"])
    assert _dead_soon(int(pid_file.read_text(encoding="utf-8")))
    assert not procrun.TOOL_GATE.busy
    # The gate is free again: the next tool runs.
    assert run(_runner(tmp_path).check_workspace(FakeCtx())).ok


def test_tool_prints_megabytes_memory_capped_log_complete(tmp_path: Path) -> None:
    megabytes = 4
    runner = _runner(tmp_path, davinci={"max_kept_output_lines": 50}, output_mb=megabytes)
    tracemalloc.start()
    try:
        result = run(runner.check_workspace(FakeCtx()))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result.ok and result.log_file is not None
    outcome = runner.last_outcome("check")
    assert outcome is not None
    assert outcome.lines_total > 40_000 and len(outcome.tail) == 50
    assert Path(result.log_file).stat().st_size > megabytes * 1024 * 1024
    # Far less memory than the output itself was held at any time.
    assert peak < megabytes * 1024 * 1024 / 2, f"peak {peak} bytes"


def test_non_utf8_output(tmp_path: Path) -> None:
    runner = _runner(tmp_path, non_utf8=True, stdout=["Prüfung läuft"])
    result = run(runner.check_workspace(FakeCtx()))
    assert result.ok and result.findings == ()
    outcome = runner.last_outcome("check")
    assert outcome is not None and "Prüfung läuft" in outcome.tail
    assert any("not UTF-8" in line for line in outcome.tail)
    assert result.log_file is not None
    Path(result.log_file).read_text(encoding="utf-8")  # the log itself is valid UTF-8

    named = _runner(tmp_path, davinci={"encoding": "cp1252"}, non_utf8=True)
    assert run(named.check_workspace(FakeCtx())).ok
    outcome = named.last_outcome("check")
    assert outcome is not None
    assert any(line.startswith("Größe überschritten") for line in outcome.tail)


def test_exit_code_0_with_error_lines(tmp_path: Path) -> None:
    runner = _runner(tmp_path, stdout=["checking"], stderr=["Error: port PpX has no interface"])
    result = run(runner.check_workspace(FakeCtx()))
    assert result.exit_code == 0 and not result.ok
    (finding,) = result.findings
    assert finding.code == "DVR-RUN-ERROR-LINE" and finding.file == result.log_file
    assert "Error: port PpX has no interface" in finding.message


def test_non_zero_exit_with_only_warnings_and_that_code_listed_as_success(tmp_path: Path) -> None:
    davinci = {"results": {"success_exit_codes": {"check": [0, 1]}}}
    runner = _runner(tmp_path, davinci=davinci, exit_code=1, stdout=["warning: unused port"])
    result = run(runner.check_workspace(FakeCtx()))
    assert result.ok and result.exit_code == 1
    assert [(f.code, f.severity) for f in result.findings] == [
        ("DVR-RUN-WARNING-LINE", Severity.WARNING)
    ]


def test_crash_exit_code(tmp_path: Path) -> None:
    runner = _runner(tmp_path, crash=True, crash_code=139)
    result = run(runner.generate(["Rte", "Os"], FakeCtx()))
    assert not result.ok and result.exit_code == 139
    exit_finding = next(f for f in result.findings if f.code == "DVR-RUN-EXIT")
    assert "exit code 139" in exit_finding.message and str(result.log_file) in exit_finding.message
    assert result.log_file is not None
    log = Path(result.log_file).read_text(encoding="utf-8")
    assert "[out] DVCfgCmd: writing resu" in log  # the half line before the crash is kept


def test_child_that_outlives_the_parent(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    runner = _runner(tmp_path, spawn_child_s=120, child_pid_file=pid_file, delay_s=0.6)
    result = run(runner.check_workspace(FakeCtx()))
    child = int(pid_file.read_text(encoding="utf-8"))
    assert result.ok and result.exit_code == 0
    (finding,) = result.findings
    assert finding.code == "DVR-RUN-ORPHAN" and f"(pid {child})" in finding.message
    assert _dead_soon(child)


def test_cancel_during_a_run(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    runner = _runner(tmp_path, hang=True, spawn_child_s=120, child_pid_file=pid_file)
    with Engine() as engine:
        handle = engine.submit(runner.check_workspace, name="check")
        deadline = time.monotonic() + WAIT
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        time.sleep(0.3)  # let the runner see the child at least once
        assert handle.cancel()
        with pytest.raises(JobCancelled):
            handle.result(WAIT)
    (record,) = _records(tmp_path)
    assert not _alive(record["pid"])
    assert _dead_soon(int(pid_file.read_text(encoding="utf-8")))
    assert not procrun.TOOL_GATE.busy
    (log,) = (tmp_path / "logs").glob("*.log")
    assert "cancelled" in log.read_text(encoding="utf-8")


def test_two_runs_requested_at_once_are_serialised_by_the_lock(tmp_path: Path) -> None:
    runner = _runner(tmp_path, delay_s=0.5)
    spans: list[tuple[float, float]] = []

    async def check(ctx: JobContext) -> ToolResult:
        result = await runner.check_workspace(ctx)
        spans.append((time.monotonic() - result.elapsed_s, time.monotonic()))
        return result

    async def generate(ctx: JobContext) -> ToolResult:
        result = await runner.generate(["Rte"], ctx)
        spans.append((time.monotonic() - result.elapsed_s, time.monotonic()))
        return result

    with Engine() as engine:
        handles = [engine.submit(check), engine.submit(generate)]
        results = [handle.result(WAIT) for handle in handles]
    assert all(result.ok for result in results)
    assert [record["tool"] for record in _records(tmp_path)] == ["DVWspChecker", "DVCfgCmd"]
    (first_start, first_end), (second_start, second_end) = sorted(spans)
    assert first_end - first_start >= 0.5 and second_end - second_start >= 0.5
    assert second_start >= first_end - 0.05  # the second tool started after the first ended


# ------------------------------------------------------------------ script result


def test_script_result_missing_malformed_partial(tmp_path: Path) -> None:
    event = ScriptEvent("SwcA", "RunA", "/Ct/SwcA/IB/TE_RunA", "Task_10ms")
    path = tmp_path / "result.json"
    assert codes(read_script_result(path, [event]).findings) == ["DVR-SCRIPT-RESULT-MISSING"]
    path.write_text('{"format": "porttool-script-result", "events": [', encoding="utf-8")
    assert codes(read_script_result(path, [event]).findings) == ["DVR-SCRIPT-RESULT-MALFORMED"]
    partial = {"format": "porttool-script-result", "complete": False, "events": []}
    path.write_text(json.dumps(partial), encoding="utf-8")
    result = read_script_result(path, [event])
    assert not result.ok
    assert codes(result.findings) == ["DVR-SCRIPT-RESULT-PARTIAL", "DVR-SCRIPT-EVENT-MISSING"]


def test_trigger_whose_task_is_not_in_the_result(tmp_path: Path) -> None:
    mapped = ScriptEvent("SwcA", "RunA", "/Ct/SwcA/IB/TE_RunA", "Task_10ms")
    dropped = ScriptEvent("SwcB", "RunB", "/Ct/SwcB/IB/DRE_RunB", "Task_Event")
    request, result_file = tmp_path / "request.json", tmp_path / "result.json"
    write_script_request([mapped, dropped], request)
    writer = (
        "import json, os; json.dump({'format': 'porttool-script-result', 'complete': True, "
        "'saved': True, 'events': [{'event_path': '/Ct/SwcA/IB/TE_RunA', "
        "'task': 'Task_10ms', 'status': 'mapped'}]}, "
        "open(os.environ['PORTTOOL_RESULT_JSON'], 'w', encoding='utf-8'))"
    )
    commands = {**fake_commands(), "script_task": [sys.executable, "-c", writer]}
    runner = DaVinciToolRunner(make_config(tmp_path, commands=commands))
    result = run(runner.run_script_task(request, result_file, FakeCtx()))
    assert result.exit_code == 0 and not result.ok
    (finding,) = result.findings
    assert finding.code == "DVR-SCRIPT-EVENT-MISSING" and finding.object_path == dropped.event_path
    assert "Task_Event" in finding.message and "Map it by hand" in finding.message


# ------------------------------------------------------------------ GUI open


def test_gui_process_running_with_an_injected_process_list(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    listing = [("svchost.exe", 800), ("DaVinciCFG.exe", 9120)]
    (finding,) = davinci_gui_findings(config, processes=lambda: listing)
    assert finding.code == "DVR-GUI-RUNNING"
    assert "DaVinciCFG.exe is running (pid 9120)" in finding.message
    assert "may be open" in finding.message
    assert davinci_gui_findings(config, processes=lambda: [("svchost.exe", 800)]) == ()


def test_lock_file_present(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    lock = config.repo_root / "Workspace.dcf.lck"
    lock.write_text("", encoding="utf-8")
    (finding,) = davinci_gui_findings(config, processes=lambda: [])
    assert finding.code == "DVR-GUI-LOCKFILE" and finding.file == str(lock)
    assert "next to the workspace file Workspace.dcf" in finding.message


# ------------------------------------------------------------------ how the tool is started


def test_tool_is_started_without_shell_with_closed_stdin_and_hidden_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}
    real = asyncio.create_subprocess_exec

    async def spy(*argv: str, **options: Any) -> asyncio.subprocess.Process:
        seen["argv"] = argv
        seen.update(options)
        options["creationflags"] = 0  # the flag exists on Windows only
        return await real(*argv, **options)

    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)
    runner = _runner(tmp_path)
    assert run(runner.check_workspace(FakeCtx())).ok
    assert seen["creationflags"] == 0x08000000
    assert seen["stdin"] == asyncio.subprocess.DEVNULL
    assert seen["stdout"] == asyncio.subprocess.PIPE and seen["stderr"] == asyncio.subprocess.PIPE
    assert seen["cwd"] == str(runner.config.repo_root)
    assert "shell" not in seen
    assert seen["argv"][0] == sys.executable and all(isinstance(a, str) for a in seen["argv"])
    assert seen["env"]["PORTTOOL_LOG_DIR"] == str(tmp_path / "logs")


@pytest.mark.windows_only
@pytest.mark.skipif(sys.platform != "win32", reason="cmd.exe wrappers run on Windows only")
def test_windows_process_tree_cmd_then_python_is_killed_on_timeout(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    commands = {**fake_commands(), "check": [str(cmd_wrapper("DVWspChecker")), "-w", "{dcf}"]}
    runner = _runner(
        tmp_path,
        commands=commands,
        timeouts=ToolTimeouts(workspace_check_s=2.0),
        hang=True,
        spawn_child_s=120,
        child_pid_file=pid_file,
    )
    with pytest.raises(ToolTimeoutError):
        run(runner.check_workspace(FakeCtx()))
    (record,) = _records(tmp_path)
    assert _dead_soon(record["pid"])  # python below cmd.exe
    assert _dead_soon(int(pid_file.read_text(encoding="utf-8")))


@pytest.mark.windows_only
@pytest.mark.skipif(sys.platform != "win32", reason="needs the Windows process table")
def test_windows_child_started_just_before_the_tool_ends_is_still_found(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    runner = _runner(tmp_path, spawn_child_s=120, child_pid_file=pid_file)  # no delay at all
    result = run(runner.check_workspace(FakeCtx()))
    assert "DVR-RUN-ORPHAN" in codes(result.findings)
    assert _dead_soon(int(pid_file.read_text(encoding="utf-8")))
