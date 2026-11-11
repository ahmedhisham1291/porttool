"""Tests for the root command line (porttool.cli) and the core commands (porttool.core.cli)."""

from __future__ import annotations

import importlib
import logging
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import click
import pytest
from click.testing import CliRunner, Result

import porttool
from porttool.cli import (
    EXIT_ERROR,
    EXIT_FINDINGS,
    EXIT_OK,
    EXIT_UNEXPECTED,
    RootGroup,
    echo_findings,
    exit_code_for,
    main,
    register_packages,
)
from porttool.core import log
from porttool.core.config_loader import load_config
from porttool.core.errors import GitError
from porttool.core.results import Finding, Severity
from tests.fixtures.generator import SMALL, generate

WARNING = Finding("T-WARN-001", Severity.WARNING, "only a warning", file="a.arxml")
ERROR = Finding("T-ERR-001", Severity.ERROR, "a real problem")


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    yield
    log.shutdown_logging()


@pytest.fixture
def logs(tmp_path: Path) -> Path:
    return tmp_path / "logs"


def run(logs: Path, *args: str) -> Result:
    return CliRunner().invoke(main, ["--log-dir", str(logs), *args])


def log_text(logs: Path) -> str:
    for handler in logging.getLogger("porttool").handlers:
        handler.flush()
    return (logs / "porttool.log").read_text(encoding="utf-8")


@pytest.fixture
def extra_commands() -> Iterator[None]:
    """Three commands on the root group, one per way a command can end badly."""

    @click.command("with-findings")
    @click.option("--bad", is_flag=True)
    def with_findings(bad: bool) -> None:
        raise click.exceptions.Exit(echo_findings([WARNING, ERROR] if bad else [WARNING]))

    @click.command("known-error")
    def known_error() -> None:
        raise GitError("git is not installed", detail="PATH has no git.exe")

    @click.command("crash")
    def crash() -> None:
        raise RuntimeError("something nobody expected")

    commands = (with_findings, known_error, crash)
    for command in commands:
        main.add_command(command)
    yield
    for command in commands:
        assert command.name is not None
        main.commands.pop(command.name)


# --------------------------------------------------------------------------- root group


def test_main_is_a_click_group_named_main() -> None:
    assert isinstance(main, click.Group) and isinstance(main, RootGroup)
    assert main.name == "main"


def test_version_and_help(logs: Path) -> None:
    version = run(logs, "--version")
    assert version.exit_code == EXIT_OK
    assert version.output.strip() == f"porttool, version {porttool.__version__}"
    usage = run(logs, "--help")
    assert usage.exit_code == EXIT_OK
    assert "config" in usage.output  # the command of porttool.core was discovered
    assert "--log-dir" in usage.output and "--verbose" in usage.output


def test_exit_code_0_success_with_warnings_only(logs: Path, extra_commands: None) -> None:
    result = run(logs, "with-findings")
    assert result.exit_code == EXIT_OK == 0
    assert "WARNING T-WARN-001: only a warning (a.arxml)" in result.output


def test_exit_code_1_for_findings_of_severity_error(logs: Path, extra_commands: None) -> None:
    result = run(logs, "with-findings", "--bad")
    assert result.exit_code == EXIT_FINDINGS == 1
    assert "ERROR T-ERR-001: a real problem" in result.output


def test_exit_code_2_for_a_porttool_error_without_traceback(
    logs: Path, extra_commands: None
) -> None:
    result = run(logs, "known-error")
    assert result.exit_code == EXIT_ERROR == 2
    assert result.stderr.splitlines() == [
        "[PT-E500] git is not installed",
        f"Log file: {logs / 'porttool.log'}",
    ]
    assert "Traceback" not in result.output
    text = log_text(logs)
    assert "command failed: [PT-E500] git is not installed" in text
    assert "Traceback" in text  # the details are in the log file


def test_exit_code_3_for_an_unexpected_error_without_traceback(
    logs: Path, extra_commands: None
) -> None:
    result = run(logs, "crash")
    assert result.exit_code == EXIT_UNEXPECTED == 3
    assert "Unexpected error: RuntimeError: something nobody expected" in result.stderr
    assert f"Log file: {logs / 'porttool.log'}" in result.stderr
    assert "Traceback" not in result.output
    text = log_text(logs)
    assert "unexpected error in command 'crash'" in text and "Traceback" in text


def test_wrong_command_line_is_a_usage_error(logs: Path) -> None:
    result = run(logs, "no-such-command")
    assert result.exit_code == 2
    assert "No such command" in result.output


def test_log_folder_that_cannot_be_created_is_reported(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    result = CliRunner().invoke(main, ["--log-dir", str(blocker / "logs"), "config", "check"])
    assert result.exit_code == EXIT_ERROR
    assert "[PT-E000] The log file" in result.stderr
    assert "No log file was written." in result.stderr


def test_exit_code_for() -> None:
    assert exit_code_for([]) == 0
    assert exit_code_for([WARNING, Finding("I", Severity.INFO, "note")]) == 0
    assert exit_code_for([WARNING, ERROR]) == 1


# --------------------------------------------------------------------------- command discovery


def test_register_packages_finds_cli_modules_and_survives_import_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    root = tmp_path / "fakeproduct"
    files = {
        "__init__.py": "",
        "single_module.py": "raise RuntimeError('modules are not packages: never imported')\n",
        "alpha/__init__.py": "",
        "alpha/cli.py": (
            "import click\n\n"
            "@click.command('hello')\n"
            "def hello() -> None:\n"
            "    click.echo('hello from alpha')\n\n"
            "def register_cli(group: click.Group) -> None:\n"
            "    group.add_command(hello)\n"
        ),
        "broken/__init__.py": "",
        "broken/cli.py": "import a_module_that_is_not_installed\n",
        "broken_package/__init__.py": "import another_missing_module\n",
        "broken_package/cli.py": "def register_cli(group):\n    raise AssertionError\n",
        "no_cli/__init__.py": "raise RuntimeError('must not be imported')\n",
        "no_register/__init__.py": "",
        "no_register/cli.py": "",
    }
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    monkeypatch.syspath_prepend(str(tmp_path))
    package = importlib.import_module("fakeproduct")
    group = click.Group("root")
    try:
        with caplog.at_level("WARNING", logger="porttool"):
            assert register_packages(group, package) == ["alpha"]
    finally:
        for name in [name for name in sys.modules if name.split(".")[0] == "fakeproduct"]:
            del sys.modules[name]
    assert list(group.commands) == ["hello"]
    assert CliRunner().invoke(group, ["hello"]).output == "hello from alpha\n"
    assert "fakeproduct.broken are not available" in caplog.text
    assert "a_module_that_is_not_installed" in caplog.text  # the import error is in the log
    assert "fakeproduct.broken_package are not available" in caplog.text
    assert "fakeproduct.no_register.cli has no register_cli" in caplog.text


def test_real_packages_register_their_commands() -> None:
    group = click.Group("root")
    assert "core" in register_packages(group)
    assert "config" in group.commands


# --------------------------------------------------------------------------- porttool config


def test_config_check_on_a_valid_repo(tmp_path: Path, logs: Path) -> None:
    truth = generate(tmp_path / "repo", SMALL)
    result = run(logs, "config", "check", "--repo", str(truth.repo_root))
    assert result.exit_code == EXIT_OK
    assert "1 ARXML folder(s), 3 SWC(s) in owners.csv, 0 locked SWC(s)." in result.output


def test_config_check_reports_warnings_and_verbose_shows_more(tmp_path: Path, logs: Path) -> None:
    truth = generate(tmp_path / "repo", SMALL)
    assert truth.repo_root is not None
    (truth.repo_root / "owners.csv").unlink()
    result = run(logs, "-v", "config", "check", "--repo", str(truth.repo_root))
    assert result.exit_code == EXIT_OK  # a warning is not an error
    assert "WARNING CFG-OWNERS-001" in result.stdout
    assert "INFO: config finding CFG-OWNERS-001" in result.stderr  # -v: console shows INFO


def test_config_check_without_configuration_is_exit_code_2(tmp_path: Path, logs: Path) -> None:
    result = run(logs, "config", "check", "--repo", str(tmp_path))
    assert result.exit_code == EXIT_ERROR
    assert result.stderr.startswith("[PT-E100] ")
    assert "porttool.toml does not exist" in result.stderr
    assert "Traceback" not in result.output


def test_config_discover_proposes_and_writes(tmp_path: Path, logs: Path) -> None:
    truth = generate(tmp_path / "repo", SMALL)
    assert truth.repo_root is not None
    repo = truth.repo_root
    before = load_config(repo).config
    (repo / "porttool.toml").unlink()

    listed = run(logs, "config", "discover", "--repo", str(repo))
    assert listed.exit_code == EXIT_OK
    assert f"* {repo / truth.dcf_file}" in listed.output
    assert f"* {repo / truth.ecuc_file}" in listed.output
    assert "flat extract:\n  (none found)" in listed.output
    assert not (repo / "porttool.toml").exists()  # nothing is written without --write

    written = run(logs, "config", "discover", "--repo", str(repo), "--write")
    assert written.exit_code == EXIT_OK
    assert f"written: {repo / 'porttool.toml'}" in written.output
    assert load_config(repo).config == before

    again = run(logs, "config", "discover", "--repo", str(repo), "--write")
    assert again.exit_code == EXIT_ERROR and "exists already" in again.stderr
    forced = run(logs, "config", "discover", "--repo", str(repo), "--write", "--force")
    assert forced.exit_code == EXIT_OK


def test_config_discover_in_an_empty_folder(tmp_path: Path, logs: Path) -> None:
    result = run(logs, "config", "discover", "--repo", str(tmp_path), "--write")
    assert result.exit_code == EXIT_ERROR
    assert "WARNING DISC-ARXML-002" in result.output
    assert "No ARXML folder was found" in result.stderr
    assert not (tmp_path / "porttool.toml").exists()
