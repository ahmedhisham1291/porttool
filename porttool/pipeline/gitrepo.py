"""Thin, blocking wrapper around the git executable.

Rules (see the pipeline task): an argument list and a timeout for every call, never a
shell; output captured; no configuration is set; nothing is pushed; history is never
rewritten; no repo-wide reset or clean. Every function blocks: call it from a worker
thread (``JobContext.run_blocking``).

All paths handed in and out are relative to ``repo_root`` with forward slashes. git is
always started in ``repo_root``; when that folder is a subfolder of the work tree, the
prefix git reports is stripped from (or used to filter) what git prints.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import psutil

from porttool.core.errors import GitError

_logger = logging.getLogger(__name__)

#: Keeps one git command line far below the Windows limit of 32,767 characters.
_MAX_ARG_CHARS = 6000
_IDENTITY_HINTS = (
    "please tell me who you are",
    "unable to auto-detect email address",
    "empty ident name",
    "no email was given",
    "no name was given",
    "auto-detection is disabled",
)

IDENTITY_HELP = (
    "git does not know who you are. Run these two commands once, with your own name and "
    "e-mail address, then start the apply again:\n"
    '  git config --global user.name "Your Name"\n'
    '  git config --global user.email "you@example.com"'
)


class GitMissingError(GitError):
    default_message = (
        "The git executable was not found. Install Git for Windows and make sure git.exe "
        "is on PATH."
    )


class GitTimeoutError(GitError):
    default_message = "A git command did not finish in time."


class GitCommandError(GitError):
    """git ended with a non-zero exit code."""

    def __init__(self, result: GitResult) -> None:
        output = (result.stderr.strip() or result.stdout.strip() or "no output")[-2000:]
        super().__init__(
            f"git {' '.join(result.args[:3])} failed with exit code {result.returncode}: {output}",
            detail=" ".join(result.args),
        )
        self.result = result


@dataclass(frozen=True, slots=True)
class GitResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def output(self) -> str:
        """stdout and stderr together, for messages (hook output arrives on either)."""
        parts = [part.strip() for part in (self.stdout, self.stderr) if part.strip()]
        return "\n".join(parts)


@dataclass(frozen=True, slots=True)
class StatusEntry:
    code: str  # the two status letters of ``git status --porcelain``, e.g. " M", "??"
    path: str  # relative to repo_root; for an entry outside repo_root: "../" form

    @property
    def untracked(self) -> bool:
        return self.code == "??"


def is_identity_problem(text: str) -> bool:
    lowered = text.lower()
    return any(hint in lowered for hint in _IDENTITY_HINTS)


def _chunks(paths: Sequence[str]) -> Iterator[list[str]]:
    batch: list[str] = []
    size = 0
    for path in paths:
        if batch and size + len(path) + 1 > _MAX_ARG_CHARS:
            yield batch
            batch, size = [], 0
        batch.append(path)
        size += len(path) + 1
    if batch:
        yield batch


def _kill_tree(process: subprocess.Popen[bytes]) -> None:
    try:
        children = psutil.Process(process.pid).children(recursive=True)
    except psutil.Error:
        children = []
    for child in children:
        try:
            child.kill()
        except psutil.Error:
            _logger.debug("child process %s of git was already gone", child.pid)
    process.kill()


class Git:
    """git for one repository folder. Holds no state besides the cached path prefix."""

    def __init__(
        self,
        repo_root: Path,
        *,
        timeout_s: float = 120.0,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.repo_root = Path(os.path.abspath(repo_root))
        self.timeout_s = timeout_s
        self._env = dict(env) if env is not None else None
        self._prefix: str | None = None

    # ---- running

    def executable(self) -> str | None:
        return shutil.which("git", path=(self._env or os.environ).get("PATH"))

    def run(
        self, *args: str, check: bool = True, stdin: bytes | None = None, literal: bool = True
    ) -> GitResult:
        """Run ``git <args>`` in the repo folder. ``literal``: pathspecs are plain paths."""
        executable = self.executable()
        if executable is None:
            raise GitMissingError()
        command = [executable, *(["--literal-pathspecs"] if literal else []), *args]
        env = dict(self._env if self._env is not None else os.environ)
        env["GIT_TERMINAL_PROMPT"] = "0"  # never wait for a password prompt
        env["LANGUAGE"] = "C"  # untranslated messages: some are matched below
        env["LC_MESSAGES"] = "C"
        started = time.perf_counter()
        try:
            process = subprocess.Popen(  # noqa: S603 - argument list, no shell
                command,
                cwd=self.repo_root,
                env=env,
                stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                # No console window pops up when the GUI process starts git.
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as error:
            raise GitError(
                f"git could not be started in {self.repo_root}: {error.strerror or error}",
                detail=repr(error),
            ) from error
        try:
            out, err = process.communicate(stdin, timeout=self.timeout_s)
        except subprocess.TimeoutExpired:
            _kill_tree(process)
            process.communicate()
            raise GitTimeoutError(
                f"git {' '.join(args[:3])} did not finish within {self.timeout_s:.0f} s and "
                "was stopped. See [timeouts] git_s in porttool.toml.",
                detail=" ".join(args),
            ) from None
        result = GitResult(
            args=tuple(args),
            returncode=process.returncode,
            stdout=out.decode("utf-8", errors="replace"),
            stderr=err.decode("utf-8", errors="replace"),
        )
        _logger.debug(
            "git %s -> %s in %.2f s", " ".join(args[:4]), result.returncode,
            time.perf_counter() - started,
        )
        if check and not result.ok:
            raise GitCommandError(result)
        return result

    # ---- questions

    def version(self) -> str:
        return self.run("--version").stdout.strip()

    def inside_work_tree(self) -> bool:
        result = self.run("rev-parse", "--is-inside-work-tree", check=False)
        return result.ok and result.stdout.strip() == "true"

    def prefix(self) -> str:
        """Path of repo_root inside the work tree: ``""`` or ``"sub/folder/"``."""
        if self._prefix is None:
            self._prefix = self.run("rev-parse", "--show-prefix").stdout.strip()
        return self._prefix

    def git_dir(self) -> Path:
        return Path(self.run("rev-parse", "--absolute-git-dir").stdout.strip())

    def head_commit(self) -> str | None:
        """Full hash of HEAD, or None when the repository has no commit yet."""
        result = self.run("rev-parse", "--verify", "--quiet", "HEAD^{commit}", check=False)
        return result.stdout.strip() or None if result.ok else None

    def current_branch(self) -> str | None:
        """Short name of the checked-out branch, or None for a detached HEAD."""
        result = self.run("symbolic-ref", "--quiet", "--short", "HEAD", check=False)
        return result.stdout.strip() or None if result.ok else None

    def branch_commit(self, branch: str) -> str | None:
        result = self.run(
            "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{commit}}", check=False
        )
        return result.stdout.strip() or None if result.ok else None

    def in_progress(self) -> tuple[str, ...]:
        """Names of unfinished git operations (merge, rebase, ...)."""
        git_dir = self.git_dir()
        markers = (
            ("MERGE_HEAD", "merge"),
            ("rebase-merge", "rebase"),
            ("rebase-apply", "rebase"),
            ("CHERRY_PICK_HEAD", "cherry-pick"),
            ("REVERT_HEAD", "revert"),
            ("BISECT_LOG", "bisect"),
        )
        found: list[str] = []
        for name, what in markers:
            if (git_dir / name).exists() and what not in found:
                found.append(what)
        return tuple(found)

    def identity_problem(self) -> str | None:
        """None when git can name an author and a committer, else git's own message."""
        for variable in ("GIT_AUTHOR_IDENT", "GIT_COMMITTER_IDENT"):
            result = self.run("var", variable, check=False)
            if not result.ok:
                return result.output or "git could not determine the identity"
        return None

    def _strip(self, path: str) -> str:
        prefix = self.prefix()
        if not prefix:
            return path
        if path.startswith(prefix):
            return path[len(prefix) :]
        return "../" * prefix.count("/") + path

    def status(self, pathspecs: Sequence[str] = ()) -> tuple[StatusEntry, ...]:
        """Changed and untracked (not ignored) files of the whole work tree or of pathspecs."""
        args = ["status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames"]
        if pathspecs:
            args += ["--", *pathspecs]
        fields = self.run(*args).stdout.split("\0")
        entries = [
            StatusEntry(code=item[:2], path=self._strip(item[3:]))
            for item in fields
            if len(item) > 3
        ]
        return tuple(entries)

    def tracked_files(self, commit: str) -> frozenset[str]:
        """Files of ``commit`` below repo_root."""
        out = self.run("ls-tree", "-r", "--name-only", "-z", commit).stdout
        return frozenset(item for item in out.split("\0") if item)

    def is_tracked(self, commit: str, path: str) -> bool:
        return self.run("cat-file", "-e", f"{commit}:./{path}", check=False).ok

    def is_ignored(self, path: str) -> bool:
        return self.run("check-ignore", "--quiet", "--", path, check=False).returncode == 0

    def branches_like(self, prefix: str) -> frozenset[str]:
        """Local and remote-tracking branch names that contain ``prefix`` (short form)."""
        out = self.run(
            "for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes", literal=False
        ).stdout
        names: set[str] = set()
        for line in out.splitlines():
            index = line.find(prefix)
            if index >= 0:
                names.add(line[index:])
        return frozenset(names)

    def diff_stat_cached(self, commit: str) -> str:
        return self.run("diff", "--cached", "--stat", commit).stdout.rstrip()

    # ---- changes (never repo-wide)

    def create_branch(self, name: str) -> None:
        self.run("checkout", "-b", name)

    def checkout_branch(self, name: str) -> None:
        self.run("checkout", name, "--")

    def checkout_detached(self, commit: str) -> None:
        self.run("checkout", "--detach", commit, "--")

    def delete_merged_branch(self, name: str) -> None:
        """``git branch -d``: git itself refuses when the branch holds unmerged commits."""
        self.run("branch", "-d", name)

    def restore_paths(self, commit: str, paths: Iterable[str]) -> None:
        """Index and working tree of exactly these paths back to ``commit``."""
        for batch in _chunks(sorted(set(paths))):
            self.run("checkout", commit, "--", *batch)

    def unstage_paths(self, paths: Iterable[str]) -> None:
        """Drop exactly these paths from the index (files this run created)."""
        for batch in _chunks(sorted(set(paths))):
            self.run("rm", "--cached", "--quiet", "--ignore-unmatch", "--", *batch)

    def add_paths(self, paths: Iterable[str]) -> None:
        for batch in _chunks(sorted(set(paths))):
            self.run("add", "--all", "--", *batch)

    def commit(self, message: str) -> GitResult:
        """Commit the index. Returns the result: the caller explains a refusal."""
        return self.run("commit", "--file=-", check=False, stdin=message.encode("utf-8"))
