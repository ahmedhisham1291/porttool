"""Step 9 and the git access of golden-diff. Read-only: no command here changes the repo.

git is called with an argument list and a timeout, never through a shell.
``--no-optional-locks`` keeps ``git status`` from rewriting the index file.
"""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Sequence
from typing import Any

from porttool.discovery.context import Context
from porttool.discovery.fsio import WalkResult
from porttool.discovery.proc import DEFAULT_MAX_BYTES, ProcResult, run_captured
from porttool.discovery.sections import file_type
from porttool.discovery.workspace import normalize_path

GIT_TIMEOUT_S = 120.0
MAX_GENERATED_FOLDERS = 30

#: Folder names (lower case, glob) that usually hold generated output.
#: PENDING-DISCOVERY: the real repo may name its generated folders differently.
GENERATED_FOLDER_PATTERNS: tuple[str, ...] = ("gendata*", "generated*", "*output*", "log", "logs")

_CONFIG_KEYS: tuple[str, ...] = (
    "core.autocrlf",
    "core.eol",
    "core.safecrlf",
    "core.longpaths",
    "core.ignorecase",
)
_ATTRIBUTES: tuple[str, ...] = ("text", "eol", "filter", "diff", "merge")


class Git:
    """Thin wrapper around the git executable for one work tree."""

    def __init__(
        self, repo: str, executable: str = "git", timeout_s: float = GIT_TIMEOUT_S
    ) -> None:
        self.repo = repo
        self.executable = executable
        self.timeout_s = timeout_s
        self._env = dict(os.environ)
        self._env["GIT_OPTIONAL_LOCKS"] = "0"
        self._env["GIT_TERMINAL_PROMPT"] = "0"

    def run(self, *args: str, max_bytes: int = DEFAULT_MAX_BYTES) -> ProcResult:
        argv = [self.executable, "--no-optional-locks", "-c", "core.quotepath=off", *args]
        return run_captured(
            argv, timeout_s=self.timeout_s, cwd=self.repo, env=self._env, max_bytes=max_bytes
        )

    def text(self, *args: str) -> str | None:
        """Standard output of a git command as stripped text, or None if it failed."""
        result = self.run(*args)
        if not result.ok:
            return None
        return result.stdout.decode("utf-8", errors="replace").strip()

    def fields(self, *args: str) -> list[str] | None:
        """NUL-separated output fields of a ``-z`` command, or None if it failed."""
        result = self.run(*args)
        if not result.ok:
            return None
        text = result.stdout.decode("utf-8", errors="replace")
        return [part for part in text.split("\0") if part != ""]

    def show(self, commit: str, path: str, max_bytes: int) -> ProcResult:
        """Content of ``path`` as stored in ``commit`` (no line-ending conversion)."""
        return self.run("show", "--no-textconv", f"{commit}:{path}", max_bytes=max_bytes)


def error_text(result: ProcResult) -> str:
    """One line that says why a git command failed."""
    if result.spawn_error is not None:
        return f"git could not be started ({result.spawn_error})"
    if result.timed_out:
        return f"git did not answer within {result.duration_s:.0f} s"
    first = result.stderr.decode("utf-8", errors="replace").strip().splitlines()[:1]
    return f"exit code {result.exit_code}" + (f": {first[0]}" if first else "")


def parse_status(fields: Sequence[str]) -> list[tuple[str, str]]:
    """Entries of ``git status --porcelain=v1 -z`` as (XY code, path)."""
    entries: list[tuple[str, str]] = []
    index = 0
    while index < len(fields):
        field = fields[index]
        code, path = field[:2], field[3:]
        entries.append((code, path))
        index += 2 if code[:1] in ("R", "C") else 1  # the next field is the old path
    return entries


def generated_folder_candidates(walk: WalkResult) -> list[str]:
    found = []
    for rel in list(walk.dirs) + list(walk.skipped):
        name = rel.rpartition("/")[2].lower()
        if any(fnmatch.fnmatchcase(name, pattern) for pattern in GENERATED_FOLDER_PATTERNS):
            found.append(rel)
    found.sort(key=lambda rel: (rel.count("/"), rel))
    return found


def build_git(
    git: Git, repo_root: str, walk: WalkResult, ctx: Context, own_folders: Sequence[str] = ()
) -> dict[str, Any]:
    """Describe the git state. ``own_folders`` are our output folders, never counted."""
    names = ctx.names
    version = git.run("--version")
    if version.spawn_error is not None:
        ctx.warning(
            "DISC-GIT-MISSING",
            "git could not be started. Install Git for Windows or pass --git <path to git.exe>. "
            "The git facts are missing from this report.",
        )
        return {"available": False, "is_repo": None}
    section: dict[str, Any] = {
        "available": True,
        "version": version.stdout.decode("utf-8", errors="replace").strip(),
    }
    inside = git.run("rev-parse", "--is-inside-work-tree")
    if not inside.ok or inside.stdout.strip() != b"true":
        section["is_repo"] = False
        ctx.warning(
            "DISC-GIT-NOT-REPO",
            "The repo folder is not inside a git work tree "
            f"({names.external(error_text(inside)) if not inside.ok else 'bare repository'}).",
        )
        return section
    section["is_repo"] = True
    toplevel = git.text("rev-parse", "--show-toplevel")
    same = toplevel is not None and normalize_path(toplevel).casefold() == normalize_path(
        repo_root
    ).casefold()
    section["repo_folder_is_work_tree_root"] = same
    if not same:
        ctx.info(
            "DISC-GIT-SUBFOLDER",
            "The folder given with --repo is not the top folder of the git work tree.",
        )
    branch = git.text("symbolic-ref", "--short", "-q", "HEAD")
    section["branch"] = names.token(branch) if branch else None
    section["detached_head"] = branch is None or branch == ""
    section["head_commit"] = git.text("rev-parse", "--verify", "-q", "HEAD")

    fields = git.fields("status", "--porcelain=v1", "-z", "--untracked-files=normal")
    if fields is None:
        section["clean"] = None
        ctx.warning("DISC-GIT-ERROR", "git status failed; clean or dirty is unknown.")
    else:
        own = [folder.strip("/").casefold() for folder in own_folders if folder]
        staged = unstaged = untracked = 0
        for code, path in parse_status(fields):
            key = path.strip("/").casefold()
            if any(key == folder or key.startswith(folder + "/") for folder in own):
                continue
            if code == "??":
                untracked += 1
                continue
            staged += code[0] not in (" ", "?")
            unstaged += code[1] not in (" ", "?")
        dirty = staged + unstaged + untracked
        section["clean"] = dirty == 0
        section["changes"] = {
            "staged": staged,
            "not_staged": unstaged,
            "untracked": untracked,
        }
        if dirty:
            ctx.info(
                "DISC-GIT-DIRTY",
                f"The work tree is not clean: {staged} staged, {unstaged} not staged, "
                f"{untracked} untracked (our own output folder is not counted).",
            )

    section["config"] = {key: git.text("config", "--get", key) for key in _CONFIG_KEYS}
    samples: dict[str, str] = {}
    for entry in walk.files:
        kind = file_type(entry.name)
        if kind is not None and kind not in samples:
            samples[kind] = entry.rel
    attributes: dict[str, dict[str, str]] = {}
    if samples:
        listed = git.fields("check-attr", "-z", *_ATTRIBUTES, "--", *samples.values()) or []
        by_rel = {rel: kind for kind, rel in samples.items()}
        for index in range(0, len(listed) - 2, 3):
            rel, attribute, value = listed[index : index + 3]
            kind = by_rel.get(rel, "")
            if kind:
                attributes.setdefault(kind, {})[attribute] = value
    section["attributes_of_one_sample_file_per_type"] = attributes
    section["uses_lfs_for_workspace_files"] = any(
        values.get("filter") == "lfs" for values in attributes.values()
    )
    present = {entry.rel.lower() for entry in walk.files if "/" not in entry.rel}
    section["gitignore_at_root"] = ".gitignore" in present
    section["gitattributes_at_root"] = ".gitattributes" in present

    generated: list[dict[str, Any]] = []
    for rel in generated_folder_candidates(walk)[:MAX_GENERATED_FOLDERS]:
        ignored = git.run("check-ignore", "-q", "--", rel)
        tracked = git.fields("ls-files", "-z", "--", rel)
        generated.append(
            {
                "folder": names.rel_path(rel, is_dir=True),
                "ignored": {0: True, 1: False}.get(ignored.exit_code if ignored.started else -1),
                "tracked_files": len(tracked) if tracked is not None else None,
            }
        )
    section["generated_folders"] = generated
    section["generated_folder_patterns"] = list(GENERATED_FOLDER_PATTERNS)
    return section
