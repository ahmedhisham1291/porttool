"""The git helper: argument lists, timeout, parsing, and what it must never do."""

from __future__ import annotations

import os
import re
import time
from datetime import datetime
from pathlib import Path

import pytest

from porttool.core.errors import GitError
from porttool.pipeline import gitrepo
from porttool.pipeline.gitrepo import (
    Git,
    GitCommandError,
    GitMissingError,
    GitTimeoutError,
    is_identity_problem,
)
from porttool.pipeline.transaction import next_branch_name
from tests.unit.pipeline.support import Repo

PACKAGE = Path(gitrepo.__file__).parent


def test_git_is_never_run_through_a_shell_and_only_with_harmless_subcommands() -> None:
    source = (PACKAGE / "gitrepo.py").read_text(encoding="utf-8")
    used = set(re.findall(r'self\.run\(\s*"([a-z-]+)"', source))
    allowed = {
        "--version", "rev-parse", "symbolic-ref", "var", "status", "ls-tree", "cat-file",
        "check-ignore", "for-each-ref", "diff", "checkout", "branch", "rm", "add", "commit",
    }  # fmt: skip
    assert used <= allowed, used - allowed
    # No push, no reset, no clean, no config, no history rewriting anywhere in the package.
    for file in PACKAGE.glob("*.py"):
        text = file.read_text(encoding="utf-8")
        assert "shell=True" not in text and "os.system" not in text, file.name
        for word in ('"push"', '"reset"', '"clean"', '"config"', '"--amend"', '"filter-branch"'):
            assert word not in text, f"{word} in {file.name}"
        if file.name != "cli.py":  # "porttool init --force" overwrites porttool.toml, not git
            assert '"--force"' not in text, file.name
    assert "subprocess.Popen" in source and "timeout=self.timeout_s" in source


def test_git_missing_is_a_git_error_with_install_advice(tmp_path: Path) -> None:
    git = Git(tmp_path, env={"PATH": str(tmp_path)})
    assert git.executable() is None
    with pytest.raises(GitMissingError, match="Install Git for Windows"):
        git.run("status")


def test_git_timeout_stops_the_process_tree(tmp_path: Path) -> None:
    fake = tmp_path / "bin" / "git"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\nsleep 30 &\nwait\n", encoding="utf-8")
    os.chmod(fake, 0o755)
    git = Git(tmp_path, timeout_s=0.3, env={"PATH": f"{fake.parent}{os.pathsep}/usr/bin:/bin"})
    started = time.perf_counter()
    with pytest.raises(GitTimeoutError, match=r"\[timeouts\] git_s"):
        git.run("status")
    assert time.perf_counter() - started < 10


def test_failed_command_raises_with_gits_own_output(repo: Repo) -> None:
    git = Git(repo.root, env=repo.env)
    with pytest.raises(GitCommandError, match="exit code") as caught:
        git.run("checkout", "no-such-branch", "--")
    assert isinstance(caught.value, GitError) and caught.value.result.returncode != 0
    assert git.run("checkout", "no-such-branch", "--", check=False).ok is False


def test_questions_about_the_repository(repo: Repo) -> None:
    git = Git(repo.root, env=repo.env)
    assert "git version" in git.version()
    assert git.inside_work_tree() and git.prefix() == ""
    assert git.head_commit() == repo.head() and git.current_branch() == "main"
    assert git.branch_commit("main") == repo.head() and git.branch_commit("nope") is None
    assert git.in_progress() == () and git.identity_problem() is None
    tracked = git.tracked_files(repo.head())
    assert "porttool.toml" in tracked and "Config/Developer/Synthetic.dcf" in tracked
    assert git.is_tracked(repo.head(), "porttool.toml")
    assert not git.is_tracked(repo.head(), "nope.txt")
    assert git.status() == ()
    odd = repo.root / "Config/Developer/new [1] ü.txt"
    odd.write_text("x", encoding="utf-8")
    (repo.root / "locked.txt").write_text("changed\n", encoding="utf-8")
    entries = {entry.path: entry for entry in git.status()}
    assert entries["Config/Developer/new [1] ü.txt"].untracked
    assert entries["locked.txt"].code == " M" and not entries["locked.txt"].untracked
    # Pathspecs are literal: brackets are not a pattern.
    assert [e.path for e in git.status(["Config/Developer/new [1] ü.txt"])] == [
        odd.relative_to(repo.root).as_posix()
    ]
    git.restore_paths(repo.head(), ["locked.txt"])
    assert [e.path for e in git.status()] == ["Config/Developer/new [1] ü.txt"]
    (repo.root / ".gitignore").write_text("*.txt\n", encoding="utf-8")
    assert git.is_ignored("Config/Developer/new [1] ü.txt") and not git.is_ignored("porttool.toml")
    repo.git("branch", "porttool/20260101-3")
    assert git.branches_like("porttool/") == frozenset({"porttool/20260101-3"})


def test_detached_head_and_a_repository_without_commits(repo: Repo, tmp_path: Path) -> None:
    git = Git(repo.root, env=repo.env)
    git.checkout_detached(repo.head())
    assert git.current_branch() is None and git.head_commit() == repo.head()
    git.checkout_branch("main")
    assert git.current_branch() == "main"
    empty = tmp_path / "empty"
    empty.mkdir()
    fresh = Git(empty, env=repo.env)
    assert not fresh.inside_work_tree()
    fresh.run("init", "--quiet")
    assert fresh.inside_work_tree() and fresh.head_commit() is None


def test_many_paths_are_split_over_several_command_lines() -> None:
    paths = [f"Config/Developer/ComponentTypes/Component{n:05d}.arxml" for n in range(1000)]
    batches = list(gitrepo._chunks(paths))
    assert len(batches) > 1 and [p for batch in batches for p in batch] == paths
    assert all(sum(len(p) + 1 for p in batch) <= 6000 for batch in batches)


@pytest.mark.parametrize(
    "text",
    [
        "*** Please tell me who you are.\n\nRun\n  git config --global user.email",
        "fatal: unable to auto-detect email address (got 'me@pc.(none)')",
        "fatal: empty ident name (for <me@pc>) not allowed",
        "fatal: no email was given and auto-detection is disabled",
    ],
)
def test_identity_problems_are_recognised(text: str) -> None:
    assert is_identity_problem(text)
    assert not is_identity_problem("pre-commit hook failed: trailing whitespace")


def test_next_branch_name_counts_per_day() -> None:
    day = datetime(2026, 11, 12, 9, 30)
    assert next_branch_name(frozenset(), day) == "porttool/20261112-1"
    taken = frozenset({"porttool/20261112-1", "porttool/20261112-7", "porttool/20261111-9",
                       "porttool/20261112-x", "porttool/other"})  # fmt: skip
    assert next_branch_name(taken, day) == "porttool/20261112-8"
