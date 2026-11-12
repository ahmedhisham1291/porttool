"""``porttool davinci ...`` through the root command line, with the fake tools."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import click
import pytest
from click.testing import CliRunner, Result

from porttool.cli import main
from porttool.core.config_loader import save_config
from porttool.core.log import shutdown_logging
from porttool.davinci.cli import register_cli
from tests.unit.davinci.helpers import fake_vars, make_config


@pytest.fixture(autouse=True)
def _close_application_log() -> Iterator[None]:
    """The root command sets up logging; undo it so that no test sees another's log file."""
    yield
    shutdown_logging()


def _repo(tmp_path: Path, **options: Any) -> Path:
    """A repo folder with a porttool.toml whose [davinci] table runs the fake tools."""
    config_options = {
        key: options.pop(key) for key in ("davinci", "commands", "pipeline") if key in options
    }
    options.setdefault("record_file", tmp_path / "record.jsonl")
    config = make_config(tmp_path, env=fake_vars(**options), **config_options)
    (config.repo_root / "arxml").mkdir()
    config = dataclasses.replace(config, arxml_roots=(config.repo_root / "arxml",))
    save_config(config)
    return config.repo_root


def _invoke(tmp_path: Path, *args: str) -> Result:
    return CliRunner().invoke(main, ["--log-dir", str(tmp_path / "applog"), "davinci", *args])


def _records(tmp_path: Path) -> list[dict[str, Any]]:
    path = tmp_path / "record.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_register_cli_adds_the_davinci_group() -> None:
    group = click.Group("root")
    register_cli(group)
    davinci = group.commands["davinci"]
    assert isinstance(davinci, click.Group)
    assert sorted(davinci.commands) == ["check", "doctor", "generate", "show-commands", "validate"]


def test_doctor_prints_findings_and_starts_nothing(tmp_path: Path) -> None:
    repo = _repo(tmp_path, commands={"check": ["{exe}", "-w", "{dcf}"]})
    result = _invoke(tmp_path, "doctor", "--repo", str(repo))
    assert result.exit_code == 0, result.output
    assert "WARNING DVR-EXE-MISSING: DVWspChecker.exe was not found" in result.output
    assert "WARNING DVR-TPL-UNVERIFIED: The command line of the action generate" in result.output
    assert "INFO DVR-DOC-TPL: The command line of the action check" in result.output
    assert "INFO DVR-DOC-NO-RUN" in result.output
    assert _records(tmp_path) == []


def test_doctor_exits_with_1_when_an_enabled_step_cannot_run(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path,
        commands={"check": ["{exe}", "-w", "{dcf}"]},
        pipeline={"davinci_steps": ["check"]},
    )
    result = _invoke(tmp_path, "doctor", "--repo", str(repo))
    assert result.exit_code == 1, result.output
    assert "ERROR DVR-EXE-MISSING" in result.output


def test_show_commands_prints_each_command_and_runs_nothing(tmp_path: Path) -> None:
    repo = _repo(tmp_path, commands={"validate": ["{exe}", "{dpa}"], "check": ["py", "{dcf}"]})
    result = _invoke(tmp_path, "show-commands", "--repo", str(repo))
    assert result.exit_code == 0, result.output
    assert "check (DVWspChecker): set in porttool.toml" in result.output
    assert f"command:  py {repo / 'Workspace.dcf'}" in result.output
    assert "it would not be started as it is:" in result.output
    assert "ERROR DVR-EXE-MISSING: The program 'py'" in result.output
    assert "generate (DVCfgCmd): UNVERIFIED built-in guess" in result.output
    assert "template: ['{exe}', '-p', '{dpa}', '-g', '-m', '{modules_csv}']" in result.output
    assert "command:  cannot be built yet:" in result.output
    assert "ERROR DVR-EXE-MISSING: DVCfgCmd.exe was not found" in result.output
    assert "Nothing was started." in result.output
    assert _records(tmp_path) == []


def test_check_runs_the_tool_and_shows_its_output(tmp_path: Path) -> None:
    repo = _repo(tmp_path, stdout=["Checking workspace", "All fine"])
    result = _invoke(tmp_path, "check", "--repo", str(repo))
    assert result.exit_code == 0, result.output
    assert "Starting DVWspChecker (action check)" in result.output
    assert "DVWspChecker: ok (exit code 0, " in result.output
    assert "Complete output: " in result.output
    (record,) = _records(tmp_path)
    assert record["tool"] == "DVWspChecker" and record["cwd"] == str(repo)
    log = Path(result.output.split("Complete output: ")[1].splitlines()[0])
    assert "[out] All fine" in log.read_text(encoding="utf-8")


def test_check_with_error_lines_exits_with_1(tmp_path: Path) -> None:
    repo = _repo(tmp_path, stdout=["ERROR: dangling reference"])
    result = _invoke(tmp_path, "check", "--repo", str(repo))
    assert result.exit_code == 1, result.output
    assert "ERROR DVR-RUN-ERROR-LINE: DVWspChecker, line " in result.output
    assert "DVWspChecker: FAILED (exit code 0, " in result.output


def test_validate_runs_dvcfgcmd(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    result = _invoke(tmp_path, "validate", "--repo", str(repo))
    assert result.exit_code == 0, result.output
    assert _records(tmp_path)[0]["args"][-1] == "--validate"


def test_generate_with_modules_option(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    result = _invoke(tmp_path, "generate", "--repo", str(repo), "--modules", "Rte, Os")
    assert result.exit_code == 0, result.output
    assert _records(tmp_path)[0]["args"][-1] == "Rte,Os"


def test_generate_without_option_uses_the_configured_modules(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    result = _invoke(tmp_path, "generate", "--repo", str(repo))
    assert result.exit_code == 0, result.output
    assert _records(tmp_path)[0]["args"][-1] == "Rte,Os"


def test_tool_that_was_not_started_exits_with_1(tmp_path: Path) -> None:
    repo = _repo(tmp_path, commands={"check": "not a list"})
    result = _invoke(tmp_path, "check", "--repo", str(repo))
    assert result.exit_code == 1, result.output
    assert "ERROR DVR-TPL-NOT-LIST" in result.output
    assert "DVWspChecker: FAILED (not started, " in result.output


def test_license_problem_exits_with_2_and_no_traceback(tmp_path: Path) -> None:
    repo = _repo(tmp_path, license_error="License not found")
    result = _invoke(tmp_path, "generate", "--repo", str(repo))
    assert result.exit_code == 2, result.output
    assert "[PT-E620]" in result.output and "License not found" in result.output
    assert "Traceback" not in result.output


def test_repo_without_porttool_toml_exits_with_2(tmp_path: Path) -> None:
    result = _invoke(tmp_path, "doctor", "--repo", str(tmp_path))
    assert result.exit_code == 2, result.output
    assert "[PT-E100]" in result.output
