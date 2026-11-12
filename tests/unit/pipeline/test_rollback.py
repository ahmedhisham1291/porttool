"""Rollback of a failed or cancelled apply, and recovery after a crash."""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

import pytest

from porttool.core.errors import GitError, RepoStateError, WriteError
from porttool.core.fsutil import RepoLock
from porttool.core.results import RequestStatus, Severity
from porttool.core.sheets import Sheet
from porttool.pipeline import PipelineError, RecoveryNeededError, codes
from porttool.pipeline.gitrepo import Git
from porttool.pipeline.journal import read_journal
from tests.unit.pipeline.crash_child import DIED, ROWS
from tests.unit.pipeline.support import (
    FakeWriter,
    Harness,
    Repo,
    error_finding,
    make_service,
    port_row,
    row,
)

ROLLED_BACK_STEPS = ["branch", "pending", "write", "verify", "packages", "report", "commit"]


def _report_codes(harness: Harness) -> list[str]:
    report = harness.service.last_report()
    assert report is not None
    return [finding.code for finding in report.findings]


def _assert_clean_after_rollback(harness: Harness, before: dict[str, object]) -> None:
    assert harness.repo.snapshot() == before
    assert not harness.journal_file().exists()
    assert list((harness.journal_file().parent / "archive").glob("*-rolled_back.jsonl"))
    assert harness.service.recovery_needed() is False
    assert not list(harness.lock_dir.glob("*.lock"))  # the repo lock was released


@pytest.mark.parametrize("step", ROLLED_BACK_STEPS)
@pytest.mark.parametrize("phase", ["start", "end"])
def test_failure_at_each_step_restores_the_repo(
    repo: Repo, tmp_path: Path, step: str, phase: str
) -> None:
    def hook(name: str, moment: str) -> None:
        if (name, moment) == (step, phase):
            raise RuntimeError(f"injected at {name} {moment}")

    harness = make_service(repo, tmp_path, step_hook=hook)
    try:
        harness.dry_run(ROWS)
        before = repo.snapshot()
        view = harness.apply()
        report = harness.service.last_report()
        assert report is not None
        if (step, phase) == ("commit", "end"):
            # The commit exists: the apply is complete and is not undone.
            assert view.state == "done" and report.ok and repo.branch() == report.branch
            assert codes.APPLY_LATE_CANCEL in _report_codes(harness)
            return
        assert view.state == "failed" and view.error is not None
        assert "injected" in view.error and "rolled back" in view.error
        assert not report.ok and report.branch is None
        if (step, phase) == ("branch", "start"):
            # Neither the journal nor the branch existed yet: nothing had to be undone.
            assert not report.rolled_back and repo.snapshot() == before
            assert not harness.journal_file().exists()
            return
        assert report.rolled_back
        assert codes.APPLY_FAILED in _report_codes(harness)
        assert codes.RB_DONE in _report_codes(harness)
        assert [r.status for r in report.results][:1] == [RequestStatus.ACCEPTED]
        _assert_clean_after_rollback(harness, before)
        # The failure report lies outside the repository and names the failed step.
        assert report.report_file is not None
        text = Path(report.report_file).read_text(encoding="utf-8")
        assert not Path(report.report_file).is_relative_to(repo.root)
        assert "ROLLED BACK" in text and f"Step &#x27;{step}&#x27; failed" in text
    finally:
        harness.service.close()


def test_failure_in_preflight_changes_nothing(repo: Repo, tmp_path: Path) -> None:
    def hook(name: str, moment: str) -> None:
        if name == "preflight":
            raise RuntimeError("injected")

    harness = make_service(repo, tmp_path, step_hook=hook)
    try:
        harness.dry_run(ROWS)
        before = repo.snapshot()
        view = harness.apply()
        assert view.state == "failed" and view.error and "injected" in view.error
        assert repo.snapshot() == before
        assert not harness.journal_file().exists()
        assert not list(harness.lock_dir.glob("*.lock"))
    finally:
        harness.service.close()


@pytest.mark.parametrize("step", [*ROLLED_BACK_STEPS, "close"])
def test_cancel_at_each_step_restores_the_repo(repo: Repo, tmp_path: Path, step: str) -> None:
    holder: list[Harness] = []

    def hook(name: str, moment: str) -> None:
        if (name, moment) == (step, "start"):
            service = holder[0].service
            job = next(job for job in service.jobs() if job.name == "apply")
            assert service.cancel(job.id) is True

    harness = make_service(repo, tmp_path, step_hook=hook)
    holder.append(harness)
    try:
        harness.dry_run(ROWS)
        before = repo.snapshot()
        view = harness.apply()
        report = harness.service.last_report()
        assert report is not None
        if step == "close":
            # Cancel after the commit: too late, the apply is complete.
            assert report.ok and repo.branch() == report.branch
            assert codes.APPLY_LATE_CANCEL in _report_codes(harness)
            assert harness.service.recovery_needed() is False
            return
        assert view.state == "cancelled", view
        assert not report.ok and codes.APPLY_CANCELLED in _report_codes(harness)
        if step == "branch":
            # Cancelled before the journal and the branch existed: nothing to undo.
            assert not report.rolled_back and repo.snapshot() == before
            return
        assert report.rolled_back
        _assert_clean_after_rollback(harness, before)
    finally:
        harness.service.close()


def test_cancel_in_the_middle_of_the_write_restores_the_repo(repo: Repo, tmp_path: Path) -> None:
    holder: list[Harness] = []
    writer = FakeWriter()

    def cancel(_rel: str) -> None:
        service = holder[0].service
        service.cancel(next(job for job in service.jobs() if job.name == "apply").id)

    writer.on_file = cancel
    harness = make_service(repo, tmp_path, writer=writer)
    holder.append(harness)
    try:
        harness.dry_run(ROWS)
        before = repo.snapshot()
        view = harness.apply()
        assert view.state == "cancelled"
        _assert_clean_after_rollback(harness, before)
    finally:
        harness.service.close()


def test_writer_exception_after_the_first_file_is_rolled_back(repo: Repo, tmp_path: Path) -> None:
    writer = FakeWriter(fail_with=WriteError("Swc2.arxml is locked"), fail_after_files=1)
    harness = make_service(repo, tmp_path, writer=writer)
    try:
        harness.dry_run(ROWS)
        before = repo.snapshot()
        view = harness.apply()
        assert view.state == "failed" and view.error and "Swc2.arxml is locked" in view.error
        _assert_clean_after_rollback(harness, before)
    finally:
        harness.service.close()


def test_writer_error_finding_fails_the_apply_as_data(repo: Repo, tmp_path: Path) -> None:
    writer = FakeWriter(error_finding=error_finding("WRT-001", "fingerprint mismatch"))
    harness = make_service(repo, tmp_path, writer=writer)
    try:
        harness.dry_run(ROWS)
        before = repo.snapshot()
        view = harness.apply()
        # An expected failure: the job is done, the summary and the report say "not ok".
        assert view.state == "done" and view.summary is not None
        assert view.summary["ok"] is False and view.summary["rolled_back"] is True
        assert {"WRT-001", codes.APPLY_WRITER} <= set(_report_codes(harness))
        _assert_clean_after_rollback(harness, before)
    finally:
        harness.service.close()


def test_writer_that_does_not_journal_is_caught_and_still_restored(
    repo: Repo, tmp_path: Path
) -> None:
    swc1 = "Config/Developer/ComponentTypes/Swc1.arxml"
    harness = make_service(repo, tmp_path, writer=FakeWriter(unjournaled=frozenset({swc1})))
    try:
        harness.dry_run(ROWS)
        before = repo.snapshot()
        harness.apply()
        assert codes.APPLY_NOT_JOURNALED in _report_codes(harness)
        _assert_clean_after_rollback(harness, before)
    finally:
        harness.service.close()


def test_files_and_folders_the_run_created_are_removed(repo: Repo, tmp_path: Path) -> None:
    def hook(name: str, moment: str) -> None:
        if (name, moment) == ("commit", "start"):
            raise RuntimeError("injected")

    harness = make_service(repo, tmp_path, step_hook=hook)
    try:
        rows = [
            row(Sheet.INTERFACES, 1, name="IfNew", kind="sender-receiver"),
            row(
                Sheet.INTERFACE_ELEMENTS,
                2,
                interface="IfNew",
                element="De1",
                data_type="T_Val001",
                queued="N",
            ),  # fmt: skip
            port_row("Swc3", "RpModel", "R", "IfSig1", 3),
        ]
        harness.dry_run(rows)
        outcome = harness.service.last_validation()
        assert outcome is not None
        assert [op.op for op in outcome.change_set.operations] == ["add_interface", "add_port"]
        before = repo.snapshot()
        harness.apply()
        # A new ARXML file in a new folder, the new pending file in a new folder and the
        # report folder existed for a while; all of it is gone again, folders included.
        _assert_clean_after_rollback(harness, before)
        assert not (repo.root / "Config/Developer/PortInterfaces/New").exists()
        assert not (repo.root / "porttool").exists()
        assert not (repo.root / "porttool_reports").exists()
    finally:
        harness.service.close()


def test_untracked_pending_file_is_restored_byte_for_byte(repo: Repo, tmp_path: Path) -> None:
    pending = repo.root / "porttool/pending.json"
    pending.parent.mkdir()
    original = b'{"format_version": 1,\r\n   "entries": []}'
    pending.write_bytes(original)  # exists, but is not in git

    def hook(name: str, moment: str) -> None:
        if (name, moment) == ("commit", "start"):
            raise RuntimeError("injected")

    harness = make_service(repo, tmp_path, step_hook=hook)
    try:
        harness.dry_run([port_row("Swc3", "RpModel", "R", "IfSig1")])
        before = repo.snapshot()
        harness.apply()
        report = harness.service.last_report()
        assert report is not None and report.rolled_back
        assert codes.PRE_UNTRACKED_OTHER in _report_codes(harness)
        assert pending.read_bytes() == original
        assert repo.snapshot() == before
    finally:
        harness.service.close()


def test_detached_head_returns_to_the_commit_after_a_failure(repo: Repo, tmp_path: Path) -> None:
    repo.git("checkout", "--quiet", "--detach", "HEAD")

    def hook(name: str, moment: str) -> None:
        if (name, moment) == ("verify", "end"):
            raise RuntimeError("injected")

    harness = make_service(repo, tmp_path, step_hook=hook)
    try:
        harness.dry_run(ROWS)
        before = repo.snapshot()
        harness.apply()
        assert repo.branch() == "HEAD"  # detached, as before
        _assert_clean_after_rollback(harness, before)
    finally:
        harness.service.close()


def test_second_apply_while_one_runs_is_refused(repo: Repo, tmp_path: Path) -> None:
    reached, release = threading.Event(), threading.Event()

    def hook(name: str, moment: str) -> None:
        if (name, moment) == ("write", "start"):
            reached.set()
            assert release.wait(60)

    harness = make_service(repo, tmp_path, step_hook=hook)
    try:
        harness.dry_run(ROWS)
        first = harness.service.start_apply()
        assert reached.wait(60)
        for start in (
            harness.service.start_apply,
            harness.service.start_rollback,
            harness.service.start_load,
            lambda: harness.service.start_dry_run_rows(ROWS),
        ):
            with pytest.raises(PipelineError, match="apply or rollback is running"):
                start()
        assert harness.service.recovery_needed() is False  # the open journal is our own
        release.set()
        view = harness.run(first)
        assert view.state == "done" and view.summary is not None and view.summary["ok"] is True
    finally:
        release.set()
        harness.service.close()


def test_apply_and_rollback_are_refused_while_another_instance_holds_the_lock(
    harness: Harness,
) -> None:
    harness.dry_run(ROWS)
    before = harness.repo.snapshot()
    with RepoLock(harness.repo.root, lock_dir=harness.lock_dir):
        view = harness.apply()
        assert view.summary is not None and view.summary["ok"] is False
        assert _report_codes(harness) == [codes.PRE_LOCK]
        view = harness.run(harness.service.start_rollback())
        assert view.summary is not None and view.summary["ok"] is False
        assert _report_codes(harness) == [codes.PRE_LOCK]
    assert harness.repo.snapshot() == before


def test_rollback_without_an_unfinished_apply_does_nothing(harness: Harness) -> None:
    before = harness.repo.snapshot()
    view = harness.run(harness.service.start_rollback())
    assert view.state == "done" and view.summary is not None and view.summary["ok"] is True
    assert _report_codes(harness) == [codes.RB_NOTHING]
    assert harness.repo.snapshot() == before


def test_rollback_that_cannot_finish_keeps_the_journal_for_a_later_recovery(
    repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def hook(name: str, moment: str) -> None:
        if (name, moment) == ("verify", "start"):
            raise RuntimeError("injected")

    harness = make_service(repo, tmp_path, step_hook=hook)
    try:
        harness.dry_run(ROWS)
        before = repo.snapshot()
        original = Git.restore_paths

        def broken(self: Git, commit: str, paths: object) -> None:
            raise GitError("index.lock exists")

        monkeypatch.setattr(Git, "restore_paths", broken)
        view = harness.apply()
        assert view.state == "failed" and view.error and "did NOT finish" in view.error
        assert codes.RB_FAILED in _report_codes(harness)
        assert harness.service.recovery_needed() is True
        with pytest.raises(RecoveryNeededError):
            harness.service.start_apply()
        monkeypatch.setattr(Git, "restore_paths", original)
        view = harness.run(harness.service.start_rollback())
        assert view.summary is not None and view.summary["ok"] is True
        assert view.summary["rolled_back"] is True
        _assert_clean_after_rollback(harness, before)
    finally:
        harness.service.close()


# --------------------------------------------------------------------------- kill and recover


def _kill_child(repo: Repo, tmp_path: Path, step: str, phase: str) -> None:
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.unit.pipeline.crash_child",
            str(repo.root),
            str(tmp_path),
            str(tmp_path / "home"),
            step,
            phase,
        ],  # fmt: skip
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert done.returncode == DIED, f"{done.returncode}\n{done.stdout}\n{done.stderr}"


@pytest.mark.parametrize(
    ("step", "phase"),
    [
        ("branch", "end"),
        ("pending", "end"),
        ("write", "file"),
        ("write", "end"),
        ("verify", "end"),
        ("packages", "end"),
        ("report", "end"),
        ("commit", "start"),
    ],
)
def test_kill_after_each_step_then_recovery_from_the_journal_alone(
    repo: Repo, tmp_path: Path, step: str, phase: str
) -> None:
    before = repo.snapshot()
    _kill_child(repo, tmp_path, step, phase)
    assert repo.snapshot() != before  # the dead process left its work behind

    # A new process: nothing but the journal file knows what happened.
    harness = make_service(repo, tmp_path, open_repo=False)
    try:
        findings = harness.service.open_repo(repo.root)
        assert codes.REC_NEEDED in [f.code for f in findings]
        assert harness.service.recovery_needed() is True
        for start in (
            harness.service.start_apply,
            harness.service.start_load,
            lambda: harness.service.start_export(tmp_path / "x.xlsx"),
            lambda: harness.service.start_dry_run_rows(ROWS),
        ):
            with pytest.raises(RecoveryNeededError):
                start()
        state = read_journal(harness.journal_file())
        assert state is not None and state.closed is None and state.original_branch == "main"
        view = harness.run(harness.service.start_rollback())
        report = harness.service.last_report()
        assert view.state == "done" and report is not None and report.ok and report.rolled_back
        assert codes.RB_DONE in _report_codes(harness)
        _assert_clean_after_rollback(harness, before)
        # The repository is usable again: the same requests apply cleanly now.
        harness.dry_run(ROWS)
        view = harness.apply()
        assert view.summary is not None and view.summary["ok"] is True, (
            harness.service.last_report()
        )
    finally:
        harness.service.close()


def test_kill_after_the_commit_keeps_the_branch_and_restores_the_tree(
    repo: Repo, tmp_path: Path
) -> None:
    before = repo.snapshot()
    _kill_child(repo, tmp_path, "commit", "end")
    harness = make_service(repo, tmp_path)
    try:
        assert harness.service.recovery_needed() is True
        view = harness.run(harness.service.start_rollback())
        report = harness.service.last_report()
        assert view.state == "done" and report is not None and report.rolled_back
        # Nothing committed is destroyed: the branch with the commit stays, and says so.
        assert report.branch is not None and report.branch.startswith("porttool/")
        assert codes.RB_BRANCH_KEPT in _report_codes(harness)
        after = repo.snapshot()
        assert after["branches"] == sorted([*before["branches"], report.branch])  # type: ignore[misc]
        after["branches"] = before["branches"]
        assert after == before  # files, HEAD, current branch and status are as at the start
        assert "PpCrash" in repo.git(
            "show", f"{report.branch}:Config/Developer/ComponentTypes/Swc1.arxml"
        )
        assert harness.service.recovery_needed() is False
    finally:
        harness.service.close()


def test_kill_after_close_needs_no_recovery(repo: Repo, tmp_path: Path) -> None:
    _kill_child(repo, tmp_path, "close", "end")
    harness = make_service(repo, tmp_path)
    try:
        assert harness.service.recovery_needed() is False
        assert repo.branch().startswith("porttool/")
        assert [f.severity for f in harness.service.open_repo(repo.root)].count(Severity.ERROR) == 0
    finally:
        harness.service.close()


def test_journal_torn_last_line_and_closed_but_not_archived(tmp_path: Path) -> None:
    from porttool.pipeline.journal import ApplyJournal, open_journal_file

    folder = tmp_path / "j"
    journal = ApplyJournal.create(
        folder, repo_root=tmp_path, original_branch="main", original_commit="a" * 40,
        tracked=["a.txt"], tool_version="t",
    )  # fmt: skip
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    journal.record("a.txt", "modify")
    journal.record("sub\\new.txt", "create")
    with pytest.raises(RepoStateError):  # RepoStateError: outside the repository
        journal.record("../outside.txt", "create")
    journal.release()
    with open(journal.file, "ab") as stream:
        stream.write(b'{"type": "path", "rel_path": "half')  # the process died here
    state = read_journal(journal.file)
    assert state is not None and state.closed is None
    assert [(p.rel_path, p.tracked, p.new_dirs) for p in state.paths] == [
        ("a.txt", True, ()),
        ("sub/new.txt", False, ("sub",)),
    ]
    assert open_journal_file(folder) == journal.file
    with pytest.raises(RepoStateError):
        ApplyJournal.create(folder, repo_root=tmp_path, original_branch=None,
                            original_commit="b" * 40, tracked=[], tool_version="t")  # fmt: skip
    with open(journal.file, "ab") as stream:
        stream.write(b'\n{"type": "closed", "outcome": "committed"}\n')
    assert open_journal_file(folder) is None  # closed: archived on sight, no recovery
    assert list((folder / "archive").glob("*-committed.jsonl"))
