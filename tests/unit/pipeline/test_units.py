"""Small units: pending file reading, settings, doctor, journal and restore edge cases."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from porttool.core.config import ProjectConfig
from porttool.core.errors import RepoStateError, WriteError
from porttool.core.interfaces import DaVinciRunner
from porttool.core.pending import PendingQueue
from porttool.core.results import Severity
from porttool.pipeline import PipelineError, PipelineSettings, codes, read_settings, restore
from porttool.pipeline.doctor import git_doctor
from porttool.pipeline.gitrepo import Git
from porttool.pipeline.journal import ApplyJournal, read_journal
from porttool.pipeline.pendingio import pending_path, read_pending, render_pending
from tests.unit.pipeline.crash_child import ROWS
from tests.unit.pipeline.support import Harness, Repo, git_env, make_repo, make_service
from tests.unit.pipeline.test_rollback import _kill_child

# --------------------------------------------------------------------------- pending file


def _config(tmp_path: Path) -> ProjectConfig:
    return ProjectConfig(repo_root=tmp_path)


def test_pending_file_missing_or_empty_is_an_empty_queue(tmp_path: Path) -> None:
    config = _config(tmp_path)
    missing = read_pending(config)
    assert missing.queue == PendingQueue() and not missing.exists and missing.usable
    pending_path(config).parent.mkdir()
    pending_path(config).write_text("  \n", encoding="utf-8")
    empty = read_pending(config)
    assert empty.queue == PendingQueue() and empty.exists and empty.usable


def test_pending_file_round_trips_with_a_byte_order_mark(tmp_path: Path) -> None:
    config = _config(tmp_path)
    pending_path(config).parent.mkdir()
    text = render_pending(PendingQueue())
    assert text.endswith("}\n") and "\r" not in text
    pending_path(config).write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))
    assert read_pending(config).usable


@pytest.mark.parametrize(
    ("content", "code", "words"),
    [
        (b"\xff\xfe\x00bad", codes.PEND_MALFORMED, "not UTF-8"),
        (b'{"format_version": 99, "entries": []}', codes.PEND_MALFORMED, "Update PortTool"),
        (b"[1, 2]", codes.PEND_MALFORMED, "not a valid pending queue"),
        (b"||||||| base\n{}\n", codes.PEND_CONFLICT, "merge-conflict markers"),
    ],
)
def test_pending_file_problems_are_findings(
    tmp_path: Path, content: bytes, code: str, words: str
) -> None:
    config = _config(tmp_path)
    pending_path(config).parent.mkdir()
    pending_path(config).write_bytes(content)
    read = read_pending(config)
    (finding,) = read.findings
    assert finding.code == code and words in finding.message and not read.usable
    assert finding.file == "porttool/pending.json" and read.queue == PendingQueue()


def test_pending_path_that_cannot_be_read_is_a_finding(tmp_path: Path) -> None:
    config = _config(tmp_path)
    pending_path(config).mkdir(parents=True)  # a folder where the file should be
    (finding,) = read_pending(config).findings
    assert finding.code == codes.PEND_UNREADABLE


# --------------------------------------------------------------------------- settings


def test_settings_defaults_are_the_safe_ones(tmp_path: Path) -> None:
    settings, findings = read_settings(_config(tmp_path))
    assert findings == () and settings == PipelineSettings()
    assert settings.davinci_steps == () and not settings.any_davinci_step
    assert not settings.fallback_import and not settings.allow_delete
    assert not settings.commit_reports and settings.commit_generated
    assert settings.report_dir == "porttool_reports" and settings.log_tail_lines == 200


def test_settings_are_read_and_checked(tmp_path: Path) -> None:
    table = {
        "davinci_steps": ["validate", "check"],
        "fallback_import": True,
        "allow_delete": 1,
        "log_tail_lines": 0,
        "report_dir": ["x"],
        "tool_write_scopes": ["Config", "Appl/GenData"],
        "journal_dir": "D:/journals",
    }
    config = ProjectConfig(repo_root=tmp_path, extras={"pipeline": table})
    settings, findings = read_settings(config)
    assert settings.davinci_steps == ("check", "validate")  # run order, not file order
    assert settings.fallback_import and settings.enabled("check") and not settings.enabled("script")
    assert settings.tool_write_scopes == ("Config", "Appl/GenData")
    assert settings.journal_dir == "D:/journals"
    assert settings.allow_delete is False and settings.log_tail_lines == 200
    assert sorted(f.code for f in findings) == [codes.CFG_TYPE] * 3
    assert all(f.severity is Severity.ERROR and f.file == "porttool.toml" for f in findings)


# --------------------------------------------------------------------------- doctor


def test_git_doctor_names_each_problem(repo: Repo, tmp_path: Path) -> None:
    assert [f.severity for f in git_doctor(Git(repo.root, env=repo.env))] == [Severity.INFO]
    plain = tmp_path / "plain"
    plain.mkdir()
    not_a_repo = git_doctor(Git(plain, env=repo.env))
    assert "not inside a git work tree" in not_a_repo[-1].message
    Git(plain, env=repo.env).run("init", "--quiet")
    anonymous = git_doctor(Git(plain, env=git_env(tmp_path / "home2", identity=False)))
    messages = " | ".join(f.message for f in anonymous)
    assert "has no commit yet" in messages and "git config --global user.name" in messages
    (missing,) = git_doctor(Git(plain, env={"PATH": str(plain)}))
    assert missing.code == codes.DOC_GIT and "not found" in missing.message


@pytest.mark.parametrize("enabled", [False, True])
def test_doctor_without_the_davinci_runner(tmp_path: Path, enabled: bool) -> None:
    toml = '\n[pipeline]\ndavinci_steps = ["check"]\n' if enabled else ""
    repo = make_repo(tmp_path / "repo", tmp_path / "home", extra_toml=toml)
    harness = make_service(repo, tmp_path)

    def no_runner(_config: ProjectConfig) -> DaVinciRunner:
        raise PipelineError("porttool.davinci is not installed")

    harness.service._runner_factory = no_runner
    try:
        view = harness.run(harness.service.start_doctor())
        assert view.state == "done" and view.summary is not None
        items = view.summary["items"]
        assert isinstance(items, list)
        (item,) = [i for i in items if i["code"] == codes.DOC_RUNNER]
        # Without a DaVinci step the missing runner is only a warning.
        assert item["severity"] == ("error" if enabled else "warning")
        assert view.summary["ok"] is not enabled
    finally:
        harness.service.close()


def test_doctor_is_allowed_and_reports_when_recovery_is_needed(repo: Repo, tmp_path: Path) -> None:
    _kill_child(repo, tmp_path, "write", "end")
    harness = make_service(repo, tmp_path)
    try:
        view = harness.run(harness.service.start_doctor())
        assert view.summary is not None and view.summary["ok"] is False
        items = view.summary["items"]
        assert isinstance(items, list) and codes.REC_NEEDED in {i["code"] for i in items}
    finally:
        harness.service.close()


def test_default_components_are_imported_lazily_under_the_fixed_names(tmp_path: Path) -> None:
    from porttool.pipeline import build_service, service

    made = build_service()
    try:
        assert type(made._get_reader()).__name__ == "ArxmlReader"
        assert type(made._get_workbook()).__name__ == "XlsxWorkbookIO"
        assert type(made._get_validator()).__name__ == "ChangeValidator"
        # The writer and the DaVinci runner are built in parallel and are not in this
        # worktree: the import fails with a clear PortToolError, not an ImportError.
        for call in (
            made._get_writer,
            lambda: service._default_runner(_config(tmp_path)),
            lambda: service._default_gui_findings(_config(tmp_path)),
        ):
            try:
                call()
            except PipelineError as error:
                assert "is not available in this installation" in str(error)
    finally:
        made.close()


# --------------------------------------------------------------------------- journal


def test_journal_refuses_use_after_close_and_reports_unusable_folders(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(RepoStateError, match="cannot be created"):
        ApplyJournal.create(blocker / "sub", repo_root=tmp_path, original_branch="main",
                            original_commit="a" * 40, tracked=[], tool_version="t")  # fmt: skip
    journal = ApplyJournal.create(
        tmp_path / "j",
        repo_root=tmp_path,
        original_branch=None,
        original_commit="a" * 40,
        tracked=[],
        tool_version="t",
    )
    (tmp_path / "untracked.txt").write_text("precious", encoding="utf-8")
    journal.record("untracked.txt", "modify")
    journal.record("untracked.txt", "modify")  # a second record changes nothing
    journal.scope(".", ["untracked.txt"])
    journal.scope(".", [])
    assert journal.has("untracked.txt") and journal.covers("any/where.txt")
    assert [scope.rel_path for scope in journal.scopes()] == ["."]
    (record,) = journal.paths()
    assert record.backup is not None and not record.tracked and record.existed
    assert (journal.backup_dir / record.backup).read_text(encoding="utf-8") == "precious"
    assert journal.work_dir.is_dir()
    archived = journal.finish("committed")
    assert archived.parent.name == "archive" and not journal.backup_dir.exists()
    with pytest.raises(RepoStateError, match="already closed"):
        journal.step("late", "started")
    state = read_journal(archived)
    assert state is not None and state.original_branch is None and state.closed == "committed"
    assert state.folder == archived.parent and len(state.paths) == 1 and len(state.scopes) == 1
    with pytest.raises(RepoStateError, match="cannot be read"):
        read_journal(tmp_path / "missing.jsonl")
    (tmp_path / "empty.jsonl").write_text("not json\n", encoding="utf-8")
    assert read_journal(tmp_path / "empty.jsonl") is None


# --------------------------------------------------------------------------- restore


def test_remove_file_handles_read_only_files_folders_and_refusals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert restore._remove_file(tmp_path / "missing") is False
    assert restore._remove_file(tmp_path) is False  # a folder is never removed
    target = tmp_path / "readonly.txt"
    target.write_text("x", encoding="utf-8")
    real_unlink, calls = os.unlink, []

    def refuse_once(path: object) -> None:
        calls.append(path)
        if len(calls) == 1:
            raise PermissionError(13, "Access is denied")
        real_unlink(path)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "unlink", refuse_once)
    assert restore._remove_file(target) is True and not target.exists() and len(calls) == 2

    target.write_text("x", encoding="utf-8")

    def refuse(path: object) -> None:
        raise PermissionError(32, "The process cannot access the file")

    monkeypatch.setattr(os, "unlink", refuse)
    with pytest.raises(WriteError, match="could not be removed during the rollback"):
        restore._remove_file(target)


def test_recovery_when_the_original_branch_is_gone_leaves_a_detached_head(
    repo: Repo, tmp_path: Path
) -> None:
    start = repo.head()
    before = repo.snapshot()
    _kill_child(repo, tmp_path, "write", "end")
    repo.git("branch", "-D", "main")  # someone deleted the branch the apply started from
    harness = make_service(repo, tmp_path)
    try:
        view = harness.run(harness.service.start_rollback())
        report = harness.service.last_report()
        assert view.summary is not None and report is not None and report.rolled_back
        assert codes.RB_BRANCH_GONE in [f.code for f in report.findings]
        after = repo.snapshot()
        assert after["files"] == before["files"] and after["status"] == ""
        assert repo.head() == start and repo.branch() == "HEAD" and repo.branches() == []
    finally:
        harness.service.close()


def test_leftover_after_a_rollback_is_reported(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    def hook_free_apply() -> None:
        harness.dry_run(ROWS)
        harness.apply()

    # git "restores" nothing: the journaled paths still differ afterwards.
    monkeypatch.setattr(Git, "restore_paths", lambda self, commit, paths: None)
    harness.service._step_hook = _fail_at_verify
    hook_free_apply()
    report = harness.service.last_report()
    assert report is not None and not report.ok and not report.rolled_back
    leftover = next(f for f in report.findings if f.code == codes.RB_LEFTOVER)
    assert "Config/Developer/ComponentTypes/Swc1.arxml" in leftover.message


def _fail_at_verify(name: str, moment: str) -> None:
    if (name, moment) == ("verify", "start"):
        raise RuntimeError("injected")
