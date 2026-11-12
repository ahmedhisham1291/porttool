"""The pipeline commands, run with click's CliRunner against generated git repositories."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from porttool.core.config_loader import load_config
from porttool.pipeline import cli, codes
from porttool.pipeline import service as service_module
from tests.unit.pipeline.crash_child import DIED
from tests.unit.pipeline.support import (
    BAD_REQUEST,
    PORT_REQUEST,
    FakeRunner,
    FakeWriter,
    Repo,
    edited_workbook,
    make_service,
)
from tests.unit.pipeline.test_rollback import _kill_child


@pytest.fixture
def group() -> click.Group:
    root = click.Group("porttool")
    cli.register_cli(root)
    return root


@pytest.fixture
def wired(repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Repo]:
    """Every service the commands build uses the fake writer and the test's state folders."""
    real = service_module.build_service

    def build() -> service_module.PortToolService:
        return real(
            writer=FakeWriter(),
            runner_factory=lambda _config: FakeRunner(),
            gui_findings=lambda _config: (),
            journal_dir=tmp_path / "journals",
            lock_dir=tmp_path / "locks",
            git_env=repo.env,
        )

    monkeypatch.setattr(service_module, "build_service", build)
    yield repo


def _workbook(repo: Repo, tmp_path: Path, *requests: object) -> Path:
    harness = make_service(repo, tmp_path)
    try:
        return edited_workbook(harness.service, tmp_path, list(requests))  # type: ignore[arg-type]
    finally:
        harness.service.close()


def test_register_cli_adds_the_six_commands_to_the_root_group(group: click.Group) -> None:
    assert sorted(group.commands) == ["apply", "dry-run", "export", "init", "rollback", "status"]
    from porttool.cli import main

    context = click.Context(main)
    for name in ("init", "export", "dry-run", "apply", "rollback", "status"):
        assert main.get_command(context, name) is not None


def test_init_discovers_paths_and_writes_porttool_toml(
    group: click.Group, repo: Repo, tmp_path: Path
) -> None:
    (repo.root / "porttool.toml").unlink()
    result = CliRunner().invoke(group, ["init", "--repo", str(repo.root)])
    assert result.exit_code == 0, result.output
    assert "Synthetic.dpa" in result.output and "Written:" in result.output
    config = load_config(repo.root).config
    assert config.dcf_file == repo.root / "Config/Developer/Synthetic.dcf"
    again = CliRunner().invoke(group, ["init", "--repo", str(repo.root)])
    assert again.exit_code == 1 and "exists already" in again.output
    forced = CliRunner().invoke(group, ["init", "--repo", str(repo.root), "--force"])
    assert forced.exit_code == 0, forced.output


def test_export_writes_the_workbook(group: click.Group, wired: Repo, tmp_path: Path) -> None:
    target = tmp_path / "out.xlsx"
    result = CliRunner().invoke(group, ["export", "--repo", str(wired.root), "--out", str(target)])
    assert result.exit_code == 0, result.output
    assert target.is_file() and f"Workbook: {target}" in result.output


def test_dry_run_prints_the_table_and_changes_nothing(
    group: click.Group, wired: Repo, tmp_path: Path
) -> None:
    workbook = _workbook(wired, tmp_path, PORT_REQUEST)
    before = wired.snapshot()
    result = CliRunner().invoke(group, ["dry-run", "--repo", str(wired.root), str(workbook)])
    assert result.exit_code == 0, result.output
    assert "accepted" in result.output and "add port /ComponentTypes/Swc1/PpCli" in result.output
    assert (
        "Plan: 1 file(s) would change" in result.output and "Nothing was changed" in result.output
    )
    assert wired.snapshot() == before


def test_dry_run_with_a_rejected_request_exits_with_1(
    group: click.Group, wired: Repo, tmp_path: Path
) -> None:
    workbook = _workbook(wired, tmp_path, PORT_REQUEST, BAD_REQUEST)
    result = CliRunner().invoke(group, ["dry-run", "--repo", str(wired.root), str(workbook)])
    assert result.exit_code == 1, result.output
    assert "rejected" in result.output and "VAL-" in result.output
    assert "Total: 1 accepted, 1 rejected" in result.output


def test_apply_with_yes_applies_and_prints_branch_and_report(
    group: click.Group, wired: Repo, tmp_path: Path
) -> None:
    workbook = _workbook(wired, tmp_path, PORT_REQUEST)
    result = CliRunner().invoke(group, ["apply", "--repo", str(wired.root), "--yes", str(workbook)])
    assert result.exit_code == 0, result.output
    assert "Applied on branch: porttool/" in result.output and "applied" in result.output
    assert "Report: " in result.output and "Committed" in result.output  # progress lines
    assert codes.APPLY_STEPS_SKIPPED in result.output  # says which DaVinci steps were skipped
    assert wired.branch().startswith("porttool/")


def test_apply_asks_for_confirmation(group: click.Group, wired: Repo, tmp_path: Path) -> None:
    workbook = _workbook(wired, tmp_path, PORT_REQUEST)
    before = wired.snapshot()
    args = ["apply", "--repo", str(wired.root), str(workbook)]
    declined = CliRunner().invoke(group, args, input="n\n")
    assert declined.exit_code == 0 and "Nothing was changed" in declined.output
    assert wired.snapshot() == before
    accepted = CliRunner().invoke(group, args, input="y\n")
    assert accepted.exit_code == 0, accepted.output
    assert wired.branch().startswith("porttool/")


def test_apply_on_a_dirty_tree_is_refused_with_exit_code_1(
    group: click.Group, wired: Repo, tmp_path: Path
) -> None:
    workbook = _workbook(wired, tmp_path, PORT_REQUEST)
    (wired.root / "Appl/GenData/Rte_Type.h").write_text("/* changed */\n", encoding="utf-8")
    result = CliRunner().invoke(group, ["apply", "--repo", str(wired.root), "--yes", str(workbook)])
    assert result.exit_code == 1, result.output
    assert codes.PRE_DIRTY in result.output and "refused" in result.output
    assert wired.branch() == "main"


def test_status_and_rollback_after_a_crash(group: click.Group, wired: Repo, tmp_path: Path) -> None:
    runner = CliRunner()
    status = runner.invoke(group, ["status", "--repo", str(wired.root)])
    assert status.exit_code == 0, status.output
    assert "Branch: main" in status.output and "No unfinished apply" in status.output
    nothing = runner.invoke(group, ["rollback", "--repo", str(wired.root)])
    assert nothing.exit_code == 0 and codes.RB_NOTHING in nothing.output

    before = wired.snapshot()
    _kill_child(wired, tmp_path, "verify", "end")
    assert DIED == 77
    status = runner.invoke(group, ["status", "--repo", str(wired.root)])
    assert status.exit_code == 1 and "RECOVERY NEEDED" in status.output
    workbook = tmp_path / "any.xlsx"
    blocked = runner.invoke(group, ["dry-run", "--repo", str(wired.root), str(workbook)])
    assert blocked.exit_code == 2 and "Roll it back first" in blocked.output
    rolled = runner.invoke(group, ["rollback", "--repo", str(wired.root)])
    assert rolled.exit_code == 0, rolled.output
    assert codes.RB_DONE in rolled.output
    assert wired.snapshot() == before
    status = runner.invoke(group, ["status", "--repo", str(wired.root)])
    assert status.exit_code == 0 and "rolled_back" in status.output


def test_a_porttool_error_gives_exit_code_2_and_no_traceback(
    group: click.Group, tmp_path: Path
) -> None:
    result = CliRunner().invoke(group, ["status", "--repo", str(tmp_path / "missing")])
    assert result.exit_code == 2
    assert "[PT-E100]" in result.output and "Traceback" not in result.output
