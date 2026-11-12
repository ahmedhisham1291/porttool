"""Restoration from the journal: shared by the rollback of a failed apply and by recovery.

Rules: only journaled paths (and paths inside journaled tool scopes) are touched; git
restores what the original commit holds; files this run created are removed; no
repo-wide reset or clean ever runs. Every function blocks and is idempotent: a
restoration that was interrupted can simply run again.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from pathlib import Path

from porttool.core.errors import WriteError
from porttool.core.fsutil import atomic_write_bytes
from porttool.core.results import Finding, Severity
from porttool.pipeline import codes
from porttool.pipeline.gitrepo import Git
from porttool.pipeline.journal import JournalState, PathRecord, append_record, archive


@dataclass(frozen=True, slots=True)
class RestoreResult:
    restored: tuple[str, ...]  # put back from the original commit
    removed: tuple[str, ...]  # created by the run, removed
    returned_to: str  # branch name, or the short commit for a detached HEAD
    branch_deleted: str | None
    branch_kept: str | None
    findings: tuple[Finding, ...]


def _norm(path: str) -> str:
    return os.path.normcase(path)


def _remove_file(path: Path) -> bool:
    """Remove one file (also a read-only one). False when it did not exist."""
    if not os.path.lexists(path):
        return False
    if path.is_dir() and not path.is_symlink():
        return False  # a journaled path is a file; a folder is left alone
    try:
        try:
            os.unlink(path)
        except PermissionError:
            os.chmod(path, stat.S_IWRITE)
            os.unlink(path)
    except OSError as error:
        raise WriteError(
            f"{path} could not be removed during the rollback: {error.strerror or error}. "
            "Close the program that uses it and run the rollback again.",
            detail=repr(error),
        ) from error
    return True


def restore_records(
    git: Git,
    commit: str,
    records: Sequence[PathRecord],
    backup_dir: Path,
    tracked: Collection[str],
) -> tuple[list[str], list[str]]:
    """Put exactly these journaled paths back. Returns (restored, removed) paths.

    ``tracked``: normcased paths of the original commit (``git ls-tree``); git decides
    what is restored by checkout, not the flag in the journal.
    """
    in_commit = [r.rel_path for r in records if _norm(r.rel_path) in tracked]
    others = [r for r in records if _norm(r.rel_path) not in tracked]
    git.restore_paths(commit, in_commit)
    removed: list[str] = []
    for record in others:
        path = git.repo_root / record.rel_path
        if record.backup is not None:
            # The file existed before the run without being in git: put the copy back.
            if (backup_dir / record.backup).is_file():
                path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_bytes(path, (backup_dir / record.backup).read_bytes())
        elif not record.existed and _remove_file(path):
            removed.append(record.rel_path)
    git.unstage_paths([r.rel_path for r in others])
    for record in records:
        for folder in record.new_dirs:  # deepest first, as journaled
            try:
                os.rmdir(git.repo_root / folder)
            except OSError:
                continue  # not empty or already gone: leave it
    return in_commit, removed


def rollback_from_journal(
    git: Git, state: JournalState, log: Callable[[str], None] = lambda _line: None
) -> RestoreResult:
    """Undo an apply from its journal alone. Blocking. Raises when git or a file refuses."""
    commit = state.original_commit
    tracked = {_norm(path) for path in git.tracked_files(commit)}
    findings: list[Finding] = []
    records = list(state.paths)
    known = {_norm(record.rel_path) for record in records}
    for scope in state.scopes:
        if git.is_ignored(scope.rel_path):
            findings.append(
                Finding(
                    code=codes.RB_SCOPE_IGNORED,
                    severity=Severity.WARNING,
                    message=f"{scope.rel_path} is ignored by git: files the DaVinci tools "
                    "wrote there cannot be restored. Generate again before you build.",
                    file=scope.rel_path,
                )
            )
            continue
        before = {_norm(path) for path in scope.untracked_before}
        for entry in git.status([scope.rel_path]):
            key = _norm(entry.path)
            if key in known or (entry.untracked and key in before):
                continue
            known.add(key)
            records.append(
                PathRecord(
                    rel_path=entry.path,
                    action="tool",
                    step=scope.step,
                    tracked=key in tracked,
                    existed=False,
                )
            )
    log(f"Restoring {len(records)} journaled path(s) to commit {commit[:12]}")
    restored, removed = restore_records(git, commit, records, state.backup_dir, tracked)

    current = git.current_branch()
    if state.original_branch is not None:
        returned = state.original_branch
        if current != state.original_branch:
            if git.branch_commit(state.original_branch) is None:
                git.checkout_detached(commit)
                returned = commit[:12]
                findings.append(
                    Finding(
                        code=codes.RB_BRANCH_GONE,
                        severity=Severity.WARNING,
                        message=f"The branch {state.original_branch} no longer exists; "
                        f"HEAD is detached at {commit[:12]}.",
                    )
                )
            else:
                log(f"Returning to branch {state.original_branch}")
                git.checkout_branch(state.original_branch)
    else:
        returned = commit[:12]
        if current is not None or git.head_commit() != commit:
            log(f"Returning to commit {commit[:12]} (detached HEAD, as before the apply)")
            git.checkout_detached(commit)

    deleted: str | None = None
    kept: str | None = None
    if state.branch is not None:
        tip = git.branch_commit(state.branch)
        if tip == commit:
            git.delete_merged_branch(state.branch)
            deleted = state.branch
            log(f"Deleted branch {state.branch} (nothing was committed on it)")
        elif tip is not None:
            kept = state.branch
            findings.append(
                Finding(
                    code=codes.RB_BRANCH_KEPT,
                    severity=Severity.WARNING,
                    message=f"The branch {state.branch} holds the commit {tip[:12]} of the "
                    "interrupted apply and was kept. Check it out to use that result, or "
                    f'delete it with: git branch -D "{state.branch}"',
                )
            )

    check = [r.rel_path for r in records if r.backup is None]
    leftover = [entry.path for entry in git.status(check)] if check else []
    if leftover:
        findings.append(
            Finding(
                code=codes.RB_LEFTOVER,
                severity=Severity.ERROR,
                message="These journaled paths still differ from the original commit: "
                + ", ".join(leftover[:20]),
            )
        )
    return RestoreResult(
        restored=tuple(restored),
        removed=tuple(removed),
        returned_to=returned,
        branch_deleted=deleted,
        branch_kept=kept,
        findings=tuple(findings),
    )


def close_rolled_back(state: JournalState, result: RestoreResult) -> Path:
    """Mark the journal as rolled back and archive it. Returns the archived file."""
    append_record(
        state.file,
        {
            "type": "note",
            "event": "rolled_back",
            "restored": len(result.restored),
            "removed": len(result.removed),
            "returned_to": result.returned_to,
            "branch_deleted": result.branch_deleted,
            "branch_kept": result.branch_kept,
        },
    )
    append_record(state.file, {"type": "closed", "outcome": "rolled_back"})
    return archive(state.file, "rolled_back")


def done_finding(result: RestoreResult) -> Finding:
    parts = [
        f"{len(result.restored)} file(s) restored from git",
        f"{len(result.removed)} created file(s) removed",
        f"back on {result.returned_to}",
    ]
    if result.branch_deleted:
        parts.append(f"branch {result.branch_deleted} deleted")
    return Finding(
        code=codes.RB_DONE,
        severity=Severity.INFO,
        message="The repository was restored: " + ", ".join(parts) + ".",
    )
