"""Preflight of the apply: every reason to refuse, and that a refusal changes nothing."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from porttool.core.results import Finding, Severity
from porttool.core.rowid import row_id
from porttool.core.sheets import Sheet
from porttool.pipeline import build_service, codes
from porttool.pipeline.gitrepo import IDENTITY_HELP
from tests.unit.pipeline.crash_child import ROWS
from tests.unit.pipeline.support import (
    FakeRunner,
    FakeWriter,
    Harness,
    Repo,
    error_finding,
    git_env,
    make_repo,
    make_service,
    port_row,
    row,
)

SWC1 = "Config/Developer/ComponentTypes/Swc1.arxml"


def _refused(harness: Harness, code: str) -> list[Finding]:
    """Apply, expect a refusal with ``code`` and an unchanged repository."""
    before = harness.repo.snapshot()
    view = harness.apply()
    report = harness.service.last_report()
    assert view.state == "done" and view.summary is not None and report is not None
    assert view.summary["ok"] is False and not report.ok and not report.rolled_back
    assert report.branch is None
    errors = [f for f in report.findings if f.severity is Severity.ERROR]
    assert code in [f.code for f in errors], report.findings
    assert harness.repo.snapshot() == before
    assert not harness.journal_file().exists()
    assert not list(harness.lock_dir.glob("*.lock"))
    return list(report.findings)


def test_apply_without_a_dry_run_is_refused(harness: Harness) -> None:
    _refused(harness, codes.PRE_NO_DRY_RUN)


def test_dirty_tree_is_refused(harness: Harness) -> None:
    harness.dry_run(ROWS)
    (harness.repo.root / "Appl/GenData/Rte_Type.h").write_text("/* edited */\n", encoding="utf-8")
    (harness.repo.root / "locked.txt").unlink()
    findings = _refused(harness, codes.PRE_DIRTY)
    message = next(f.message for f in findings if f.code == codes.PRE_DIRTY)
    assert "2 uncommitted change(s)" in message and "Appl/GenData/Rte_Type.h" in message


def test_untracked_files_in_the_workspace_folders_are_refused(harness: Harness) -> None:
    harness.dry_run(ROWS)
    (harness.repo.root / "Config/Developer/Scratch.txt").write_text("x", encoding="utf-8")
    (harness.repo.root / "notes.txt").write_text("x", encoding="utf-8")
    findings = _refused(harness, codes.PRE_UNTRACKED_WORKSPACE)
    by_code = {f.code: f for f in findings}
    assert "Config/Developer/Scratch.txt" in by_code[codes.PRE_UNTRACKED_WORKSPACE].message
    assert "notes.txt" not in by_code[codes.PRE_UNTRACKED_WORKSPACE].message
    # Untracked files elsewhere are only listed, as a warning.
    other = by_code[codes.PRE_UNTRACKED_OTHER]
    assert other.severity is Severity.WARNING and "notes.txt" in other.message


def test_untracked_files_outside_the_workspace_are_a_warning_and_are_left_alone(
    harness: Harness,
) -> None:
    harness.dry_run(ROWS)
    notes = harness.repo.root / "notes.txt"
    notes.write_text("keep me", encoding="utf-8")
    view = harness.apply()
    report = harness.service.last_report()
    assert view.summary is not None and report is not None and report.ok
    assert codes.PRE_UNTRACKED_OTHER in [f.code for f in report.findings]
    assert notes.read_text(encoding="utf-8") == "keep me"
    assert "notes.txt" not in harness.repo.git("show", "--name-only", "--format=", "HEAD")


def test_merge_in_progress_is_refused(harness: Harness) -> None:
    repo = harness.repo
    repo.git("checkout", "--quiet", "-b", "other")
    (repo.root / "locked.txt").write_text("# other\n", encoding="utf-8")
    repo.git("commit", "--quiet", "-am", "other")
    repo.git("checkout", "--quiet", "main")
    harness.dry_run(ROWS)
    repo.git("merge", "--no-ff", "--no-commit", "other")
    findings = _refused(harness, codes.PRE_IN_PROGRESS)
    assert "merge is in progress" in next(
        f.message for f in findings if f.code == codes.PRE_IN_PROGRESS
    )


def test_git_missing_path_without_git(
    repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    service = build_service(
        writer=FakeWriter(),
        runner_factory=lambda _config: FakeRunner(),
        journal_dir=tmp_path / "journals",
        lock_dir=tmp_path / "locks",
    )
    harness = Harness(
        repo, service, FakeWriter(), FakeRunner(), tmp_path / "journals", tmp_path / "locks"
    )
    try:
        service.open_repo(repo.root)
        assert harness.run(service.start_dry_run_rows(ROWS)).state == "done"  # needs no git
        view = harness.apply()
        report = service.last_report()
        assert view.state == "done" and report is not None and not report.ok
        (error,) = [f for f in report.findings if f.severity is Severity.ERROR]
        assert error.code == codes.PRE_GIT_MISSING and "Install Git for Windows" in error.message
        doctor = harness.run(service.start_doctor())
        assert doctor.summary is not None and doctor.summary["ok"] is False
        monkeypatch.undo()  # git is back for the comparison
        assert "PpCrash" not in (repo.root / SWC1).read_text(encoding="utf-8")
    finally:
        service.close()


def test_git_user_name_not_set_is_refused_with_the_two_commands(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo", tmp_path / "home", identity=False)
    harness = make_service(repo, tmp_path)
    try:
        harness.dry_run(ROWS)
        findings = _refused(harness, codes.PRE_IDENTITY)
        message = next(f.message for f in findings if f.code == codes.PRE_IDENTITY)
        assert 'git config --global user.name "Your Name"' in message
        assert 'git config --global user.email "you@example.com"' in message
    finally:
        harness.service.close()


def test_identity_lost_before_the_commit_is_caught_and_rolled_back(
    repo: Repo, tmp_path: Path
) -> None:
    def hook(name: str, moment: str) -> None:
        if (name, moment) == ("commit", "start"):
            git_env(tmp_path / "home", identity=False)  # rewrites the global git config

    harness = make_service(repo, tmp_path, step_hook=hook)
    try:
        harness.dry_run(ROWS)
        before = repo.snapshot()
        view = harness.apply()
        report = harness.service.last_report()
        assert view.state == "done" and report is not None
        assert not report.ok and report.rolled_back
        (error,) = [f for f in report.findings if f.code == codes.COMMIT_IDENTITY]
        assert error.message.startswith(IDENTITY_HELP)
        git_env(tmp_path / "home", identity=True)
        assert repo.snapshot() == before
    finally:
        harness.service.close()


def test_a_commit_hook_that_fails_rolls_back_and_reports_the_hook_output(
    repo: Repo, tmp_path: Path
) -> None:
    hook_file = repo.root / ".git/hooks/pre-commit"
    hook_file.write_text("#!/bin/sh\necho 'policy: ARXML needs a review ticket'\nexit 1\n", "utf-8")
    os.chmod(hook_file, 0o755)
    harness = make_service(repo, tmp_path)
    try:
        harness.dry_run(ROWS)
        before = repo.snapshot()
        view = harness.apply()
        report = harness.service.last_report()
        assert view.state == "done" and report is not None
        assert not report.ok and report.rolled_back
        (error,) = [f for f in report.findings if f.code == codes.COMMIT_REJECTED]
        assert "policy: ARXML needs a review ticket" in error.message
        assert repo.snapshot() == before
        assert report.report_file is not None
        assert "policy: ARXML needs a review ticket" in Path(report.report_file).read_text("utf-8")
    finally:
        harness.service.close()


def test_stale_model_is_refused(harness: Harness) -> None:
    repo = harness.repo
    harness.dry_run(ROWS)
    swc = repo.root / SWC1
    swc.write_text(swc.read_text(encoding="utf-8") + "<!-- someone pulled -->\n", encoding="utf-8")
    repo.git("commit", "--quiet", "-am", "a colleague's change")  # the tree is clean again
    _refused(harness, codes.PRE_STALE)


def test_model_reloaded_after_the_dry_run_is_refused(harness: Harness) -> None:
    harness.dry_run(ROWS)
    assert harness.run(harness.service.start_load()).state == "done"
    _refused(harness, codes.PRE_MODEL_CHANGED)


@pytest.mark.parametrize(
    "content",
    ["{ not json", '<<<<<<< HEAD\n{"entries": []}\n=======\n{}\n>>>>>>> other\n'],
    ids=["malformed", "conflict-markers"],
)
def test_pending_file_malformed_or_with_conflict_markers_refuses_the_apply(
    repo: Repo, tmp_path: Path, content: str
) -> None:
    pending = repo.root / "porttool/pending.json"
    pending.parent.mkdir()
    pending.write_text(content, encoding="utf-8")
    repo.git("add", "--all")
    repo.git("commit", "--quiet", "-m", "broken pending file")
    harness = make_service(repo, tmp_path)
    try:
        harness.dry_run(ROWS)
        findings = _refused(harness, codes.PRE_PENDING)
        assert {codes.PEND_MALFORMED, codes.PEND_CONFLICT} & {f.code for f in findings}
        assert pending.read_text(encoding="utf-8") == content
    finally:
        harness.service.close()


def test_pending_file_changed_since_the_dry_run_is_refused(harness: Harness) -> None:
    repo = harness.repo
    harness.dry_run([port_row("Swc3", "RpModel", "R", "IfSig1")])
    outcome = harness.service.last_validation()
    assert outcome is not None
    pending = repo.root / "porttool/pending.json"
    pending.parent.mkdir()
    pending.write_text(outcome.pending.to_json(), encoding="utf-8")  # a colleague's queue
    repo.git("add", "--all")
    repo.git("commit", "--quiet", "-m", "pending from a colleague")
    findings = _refused(harness, codes.PRE_PENDING)
    assert "changed since the dry run" in next(
        f.message for f in findings if f.code == codes.PRE_PENDING
    )


def test_write_plan_with_errors_is_refused(repo: Repo, tmp_path: Path) -> None:
    writer = FakeWriter(plan_findings=(error_finding("WRT-PLAN-1", "file is read-only"),))
    harness = make_service(repo, tmp_path, writer=writer)
    try:
        view = harness.dry_run(ROWS)
        assert view.summary is not None and view.summary["can_apply"] is False
        findings = _refused(harness, codes.PRE_PLAN_ERRORS)
        assert "WRT-PLAN-1" in [f.code for f in findings]
    finally:
        harness.service.close()


def test_not_enough_free_disk_space_is_refused(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo", tmp_path / "home", min_free_disk_bytes=10**18)
    harness = make_service(repo, tmp_path)
    try:
        harness.dry_run(ROWS)
        _refused(harness, codes.PRE_DISK)
    finally:
        harness.service.close()


def test_folder_that_is_not_a_git_work_tree_is_refused(tmp_path: Path) -> None:
    from tests.fixtures.generator import SMALL, generate

    root = tmp_path / "plain"
    truth = generate(root, SMALL)
    repo = Repo(root=root, env=git_env(tmp_path / "home"), truth=truth)
    harness = make_service(repo, tmp_path)
    try:
        harness.dry_run(ROWS)
        view = harness.apply()
        report = harness.service.last_report()
        assert view.summary is not None and report is not None and not report.ok
        assert codes.PRE_NOT_A_REPO in [f.code for f in report.findings]
        assert "PpCrash" not in (root / SWC1).read_text(encoding="utf-8")
    finally:
        harness.service.close()


def test_wrong_pipeline_settings_refuse_the_apply(tmp_path: Path) -> None:
    toml = '\n[pipeline]\ndavinci_steps = ["check", "compile"]\nallow_delete = "yes"\nextra = 1\n'
    repo = make_repo(tmp_path / "repo", tmp_path / "home", extra_toml=toml)
    harness = make_service(repo, tmp_path, open_repo=False)
    try:
        opened = harness.service.open_repo(repo.root)
        assert {codes.CFG_TYPE, codes.CFG_UNKNOWN_KEY, codes.CFG_UNKNOWN_STEP} <= {
            f.code for f in opened
        }
        assert harness.service.settings().davinci_steps == ("check",)
        assert harness.service.settings().allow_delete is False
        harness.dry_run(ROWS)
        _refused(harness, codes.CFG_UNKNOWN_STEP)
    finally:
        harness.service.close()


def _delete_row(swc: str, port: str) -> object:
    return row(
        Sheet.PORTS,
        1,
        row_id=row_id(Sheet.PORTS, f"/ComponentTypes/{swc}/{port}"),
        swc=swc,
        port=port,
        direction="P",
        interface="IfSig1",
        action="DELETE",
    )


@pytest.mark.parametrize("allowed", [False, True])
def test_delete_requests_are_refused_unless_allow_delete_is_set(
    tmp_path: Path, allowed: bool
) -> None:
    toml = "\n[pipeline]\nallow_delete = true\n" if allowed else ""
    repo = make_repo(tmp_path / "repo", tmp_path / "home", extra_toml=toml)
    harness = make_service(repo, tmp_path)
    try:
        harness.dry_run([port_row("Swc1", "PpTemp", "P", "IfSig1")])
        harness.apply()
        first = harness.service.last_report()
        assert first is not None and first.ok and first.branch is not None
        harness.dry_run([_delete_row("Swc1", "PpTemp")])  # type: ignore[list-item]
        outcome = harness.service.last_validation()
        assert outcome is not None
        assert [op.op for op in outcome.change_set.operations] == ["delete_port"]
        if not allowed:
            findings = _refused(harness, codes.PRE_DELETE)
            assert "allow_delete = true" in next(
                f.message for f in findings if f.code == codes.PRE_DELETE
            )
            return
        harness.apply()
        report = harness.service.last_report()
        assert report is not None and report.ok, report
        assert report.branch == first.branch[:-1] + "2"
        model = harness.service.model()
        assert model is not None and "/ComponentTypes/Swc1/PpTemp" not in model.ports
    finally:
        harness.service.close()
