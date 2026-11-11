"""Orchestration of ``discover run`` and ``discover golden-diff``.

Every step is isolated: a step that fails becomes a finding and the next step still
runs. The report is written at the end, the JSON file last, so that it also holds the
findings of the writing itself.
"""

from __future__ import annotations

import csv
import io
import json
import os
import platform
import secrets
import sys
import time
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, TypeVar

from porttool import __version__
from porttool.core.errors import ConfigError, GitError, WriteError
from porttool.discovery import render
from porttool.discovery.arxml_scan import ArxmlFacts, scan_arxml
from porttool.discovery.context import Context, Progress
from porttool.discovery.fsio import (
    ExcludeMatcher,
    FileEntry,
    WalkResult,
    atomic_write_bytes,
    atomic_write_text,
    io_path,
    now_utc,
    read_chunks,
    remove_quietly,
    walk_repo,
)
from porttool.discovery.gitinfo import Git, build_git
from porttool.discovery.golden import GoldenOptions, build_golden_diff
from porttool.discovery.names import Names
from porttool.discovery.sections import (
    OwnersResult,
    build_composition,
    build_ecuc,
    build_inventory,
    build_lock_signs,
    build_owners,
    build_storage_map,
    build_xml_style,
)
from porttool.discovery.tools import (
    HELP_TIMEOUT_S,
    Launcher,
    ToolSearch,
    ToolsResult,
    build_tools,
    direct_launcher,
    skipped_tools_section,
)
from porttool.discovery.workspace import WorkspaceResult, build_workspace, normalize_path

FORMAT_VERSION = 1
DEFAULT_OUT_NAME = "porttool_discovery"
PRIVATE_SUFFIX = "_private"
MANIFEST_NAME = "manifest.json"
MAX_FILE_FINDINGS = 100
_PROGRESS_EVERY_S = 2.0

_T = TypeVar("_T")


@dataclass(slots=True)
class DiscoveryOptions:
    repo: str
    dpa: str | None = None
    sip: str | None = None
    developer_bin: str | None = None
    out: str | None = None
    anonymize: bool = False
    no_tools: bool = False
    exclude: tuple[str, ...] = ()
    git_executable: str = "git"
    tool_timeout_s: float = HELP_TIMEOUT_S
    #: Tests replace the next two to run fake tool scripts with the Python interpreter.
    tool_suffixes: tuple[str, ...] = (".exe",)
    tool_launcher: Launcher = direct_launcher
    environ: Mapping[str, str] | None = None


@dataclass(slots=True)
class GoldenDiffOptions:
    repo: str
    base: str
    head: str
    out: str | None = None
    anonymize: bool = False
    git_executable: str = "git"


@dataclass(slots=True)
class DiscoveryResult:
    out_dir: str
    private_dir: str | None
    report: dict[str, Any]
    files_written: list[str] = field(default_factory=list)
    summary: str = ""


# --------------------------------------------------------------------------- output folder


@dataclass(slots=True)
class OutputFolder:
    path: str
    private: str | None
    manifest: dict[str, Any]


def _check_repo(repo: str) -> str:
    root = os.path.abspath(repo)
    if not os.path.isdir(io_path(root)):
        raise ConfigError(f"The repo folder does not exist or is not a folder: {root}")
    return root


def prepare_output(repo_root: str, out: str | None, anonymize: bool) -> OutputFolder:
    """Create and test the report folder before any long work starts.

    Raises WriteError when the folder cannot be written or holds files that are not
    ours: discovery never writes into a folder the ECU build may use.
    """
    path = os.path.abspath(out) if out else os.path.join(repo_root, DEFAULT_OUT_NAME)
    advice = "Choose another folder with --out."
    manifest: dict[str, Any] = {"format_version": FORMAT_VERSION, "reports": {}}
    try:
        if os.path.lexists(io_path(path)) and not os.path.isdir(io_path(path)):
            raise WriteError(f"The output path exists and is not a folder: {path}. {advice}")
        if os.path.isdir(io_path(path)):
            existing = os.listdir(io_path(path))
            if existing and MANIFEST_NAME not in existing:
                raise WriteError(
                    f"The output folder holds files that discovery did not write: {path}. "
                    "Choose an empty or new folder with --out."
                )
            if MANIFEST_NAME in existing:
                manifest = _read_manifest(os.path.join(path, MANIFEST_NAME), manifest)
        os.makedirs(io_path(path), exist_ok=True)
        probe = os.path.join(path, ".write_test")
        atomic_write_bytes(probe, b"porttool discover write test\n")
        remove_quietly(probe)
    except OSError as error:
        raise WriteError(
            f"The output folder cannot be written ({type(error).__name__}): {path}. {advice}"
        ) from error
    return OutputFolder(path, path + PRIVATE_SUFFIX if anonymize else None, manifest)


def _read_manifest(path: str, default: dict[str, Any]) -> dict[str, Any]:
    try:
        with open(io_path(path), encoding="utf-8") as handle:
            loaded = json.load(handle)
    except (OSError, ValueError):
        return default
    if isinstance(loaded, dict) and isinstance(loaded.get("reports"), dict):
        return loaded
    return default


def _load_key(private: str, ctx: Context) -> bytes:
    """The anonymization key: kept in the private folder so tokens repeat across runs."""
    key_file = os.path.join(private, "anonymize.key")
    try:
        os.makedirs(io_path(private), exist_ok=True)
        if os.path.isfile(io_path(key_file)):
            with open(io_path(key_file), encoding="ascii") as handle:
                stored = handle.read().strip()
            if len(stored) >= 32:
                return bytes.fromhex(stored)
        key = secrets.token_hex(32)
        atomic_write_text(key_file, key + "\n")
        return bytes.fromhex(key)
    except (OSError, ValueError) as error:
        ctx.warning(
            "DISC-PRIVATE-WRITE",
            f"The private folder could not be used ({type(error).__name__}). Tokens are stable "
            "within this run only and no token map was kept.",
        )
        return secrets.token_bytes(32)


def _write_token_map(private: str, names: Names, ctx: Context) -> None:
    path = os.path.join(private, "token_map.csv")
    merged: dict[str, str] = {}
    try:
        if os.path.isfile(io_path(path)):
            with open(io_path(path), encoding="utf-8", newline="") as handle:
                for row in csv.reader(handle):
                    if len(row) == 2 and row[0] != "token":
                        merged[row[0]] = row[1]
        merged.update(names.mapping())
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(["token", "original"])
        for token in sorted(merged):
            writer.writerow([token, merged[token]])
        atomic_write_text(path, buffer.getvalue())
    except (OSError, csv.Error) as error:
        ctx.warning(
            "DISC-PRIVATE-WRITE",
            f"The private token map could not be written ({type(error).__name__}).",
        )


# --------------------------------------------------------------------------- steps


@dataclass(slots=True)
class _Steps:
    ctx: Context
    total: int
    records: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    number: int = 0

    def run(self, key: str, title: str, action: Callable[[], _T], fallback: _T) -> _T:
        """Run one step. A failure is recorded as a finding and ``fallback`` is returned."""
        self.number += 1
        ctx = self.ctx
        ctx.step = key
        before = len(ctx.findings)
        ctx.progress(f"[{self.number}/{self.total}] {title} ...")
        started = time.monotonic()
        ok = True
        result = fallback
        try:
            result = action()
        except Exception as error:  # noqa: BLE001 - step boundary: report it, keep going
            ok = False
            frames = [
                f"{os.path.basename(frame.filename)}:{frame.lineno} {frame.name}"
                for frame in traceback.extract_tb(error.__traceback__)
            ]
            self.errors.append(
                {
                    "step": key,
                    "error": type(error).__name__,
                    "message": ctx.names.external(str(error)),
                    "frames": frames,
                }
            )
            ctx.error(
                "DISC-STEP-FAILED",
                f"Step '{title}' failed with {type(error).__name__}; its part of the report is "
                "missing or incomplete. The other steps ran.",
            )
        seconds = time.monotonic() - started
        found = len(ctx.findings) - before
        self.records.append(
            {
                "step": key,
                "title": title,
                "ok": ok,
                "seconds": round(seconds, 2),
                "findings": found,
            }
        )
        state = "done" if ok else "FAILED"
        ctx.progress(f"      {state} in {seconds:.1f} s, {found} finding(s)")
        ctx.step = ""
        return result


def _no_section() -> dict[str, Any]:
    return {}


def _scan_all(walk: WalkResult, ctx: Context) -> list[ArxmlFacts]:
    entries = [entry for entry in walk.files if entry.name.lower().endswith(".arxml")]
    total_bytes = sum(entry.size for entry in entries)
    facts: list[ArxmlFacts] = []
    done_bytes = 0
    reported = 0
    last = time.monotonic()
    for number, entry in enumerate(entries, start=1):
        item = scan_arxml(entry.rel, entry.size, read_chunks(entry.path))
        facts.append(item)
        done_bytes += entry.size
        problem = item.problem
        if problem is not None:
            reported += 1
            if reported <= MAX_FILE_FINDINGS:
                code = {
                    "empty": "DISC-XML-EMPTY",
                    "malformed": "DISC-XML-MALFORMED",
                    "encoding": "DISC-XML-ENCODING",
                    "unreadable": "DISC-FILE-UNREADABLE",
                }.get(problem.kind, "DISC-XML-MALFORMED")
                ctx.warning(code, f"ARXML file skipped or read in part: {problem.describe()}.",
                            file=entry.rel)  # fmt: skip
        if time.monotonic() - last >= _PROGRESS_EVERY_S:
            last = time.monotonic()
            ctx.progress(
                f"      {number}/{len(entries)} ARXML files, "
                f"{done_bytes / 1e6:.0f} of {total_bytes / 1e6:.0f} MB"
            )
    if reported > MAX_FILE_FINDINGS:
        ctx.warning(
            "DISC-XML-MANY",
            f"{reported - MAX_FILE_FINDINGS} more ARXML files had problems; all of them are "
            "listed under inventory.problem_files.",
        )
    return facts


def _walk(root: str, matcher: ExcludeMatcher, ctx: Context) -> WalkResult:
    walk = walk_repo(root, matcher)
    for rel, error in walk.errors[:MAX_FILE_FINDINGS]:
        ctx.warning("DISC-WALK-ERROR", f"A folder or file could not be read ({error}).", file=rel)
    for rel in walk.too_deep[:MAX_FILE_FINDINGS]:
        ctx.warning("DISC-WALK-DEPTH", "A folder is nested too deeply and was not read.", file=rel)
    return walk


def _explicit_dpa(path: str | None, root: str, ctx: Context) -> FileEntry | None:
    if path is None:
        return None
    absolute = os.path.abspath(path)
    try:
        stat = os.stat(io_path(absolute))
    except OSError as error:
        ctx.warning(
            "DISC-DPA-OPTION",
            f"The file given with --dpa could not be read ({type(error).__name__}).",
        )
        return None
    prefix = normalize_path(root).casefold() + "/"
    normalized = normalize_path(absolute)
    inside = normalized.casefold().startswith(prefix)
    rel = normalized[len(prefix) :] if inside else os.path.basename(absolute)
    return FileEntry(rel, absolute, stat.st_size, stat.st_mtime_ns)


def _mode(anonymized: bool, command: str, tools_started: bool) -> dict[str, Any]:
    return {
        "anonymized": anonymized,
        "command": command,
        "vector_tools_started": tools_started,
        "includes": render.mode_statement(anonymized, command),
    }


def _header(command: str, mode: dict[str, Any]) -> dict[str, Any]:
    return {
        "format_version": FORMAT_VERSION,
        "tool": {"name": "porttool discover", "version": __version__, "command": command},
        "created_utc": now_utc(),
        "mode": mode,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "filesystem_encoding": sys.getfilesystemencoding(),
        },
    }


class _Writer:
    """Writes report files; a failure becomes a finding, except for the JSON report."""

    def __init__(self, ctx: Context, folder: str) -> None:
        self.ctx = ctx
        self.folder = folder
        self.written: list[str] = []

    def text(self, name: str, text: str, *, newline: str = "\n", folder: str | None = None) -> bool:
        base = folder if folder is not None else self.folder
        path = os.path.join(base, *name.split("/"))
        try:
            os.makedirs(io_path(os.path.dirname(path)), exist_ok=True)
            atomic_write_text(path, text, newline=newline)
        except OSError as error:
            self.ctx.error(
                "DISC-OUT-WRITE",
                f"The report file {name} could not be written ({type(error).__name__}).",
            )
            return False
        if folder is None:
            self.written.append(name)
        return True


def _clear_cli(folder: str) -> None:
    cli = os.path.join(folder, "cli")
    if not os.path.isdir(io_path(cli)):
        return
    for name in os.listdir(io_path(cli)):
        if name.lower().endswith(".txt"):
            remove_quietly(os.path.join(cli, name))


def _finish(
    output: OutputFolder,
    writer: _Writer,
    report: dict[str, Any],
    json_name: str,
    ctx: Context,
) -> None:
    """Write manifest, README and, last of all, the JSON report."""
    output.manifest["reports"][json_name] = {
        "command": report["tool"]["command"],
        "anonymized": report["mode"]["anonymized"],
        "created_utc": report["created_utc"],
        "files": sorted(writer.written),
    }
    private_name = os.path.basename(output.path) + PRIVATE_SUFFIX
    writer.text(
        "README.txt",
        render.render_readme(output.manifest, os.path.basename(output.path), private_name),
        newline="\r\n",
    )
    writer.text(MANIFEST_NAME, json.dumps(output.manifest, indent=1, ensure_ascii=False) + "\n")
    report["findings"] = ctx.findings_json()
    report["findings_by_severity"] = ctx.count_by_severity()
    path = os.path.join(output.path, json_name)
    try:
        atomic_write_text(path, json.dumps(report, indent=1, ensure_ascii=False) + "\n")
    except OSError as error:
        raise WriteError(
            f"The report could not be written ({type(error).__name__}): {path}"
        ) from error
    writer.written.append(json_name)


# --------------------------------------------------------------------------- run


def run_discovery(options: DiscoveryOptions, progress: Progress | None = None) -> DiscoveryResult:
    """Gather every fact of ``discover run`` and write the report folder.

    Raises ConfigError (bad repo folder) or WriteError (report folder unusable). Every
    other problem is a finding inside the report.
    """
    started = time.monotonic()
    root = _check_repo(options.repo)
    output = prepare_output(root, options.out, options.anonymize)
    ctx = Context(progress=progress or (lambda _text: None))
    if output.private is not None:
        ctx.names = Names(anonymize=True, key=_load_key(output.private, ctx))
    environ = options.environ if options.environ is not None else dict(os.environ)

    own_folders = [output.path] + ([output.private] if output.private else [])
    index_root = normalize_path(root).casefold() + "/"
    own_rel = [
        normalize_path(folder)[len(index_root) :]
        for folder in own_folders
        if normalize_path(folder).casefold().startswith(index_root)
    ]
    exclude = (".git", *own_rel, *options.exclude)
    matcher = ExcludeMatcher(exclude)

    steps = _Steps(ctx, total=10)
    report = _header("run", _mode(options.anonymize, "run", not options.no_tools))
    report["options"] = {
        "exclude_globs": [".git", *options.exclude],
        "dpa_given": options.dpa is not None,
        "sip_given": options.sip is not None,
        "developer_bin_given": options.developer_bin is not None,
        "no_tools": options.no_tools,
        "repo_root_path_length": len(root),
    }

    holder: dict[str, Any] = {"walk": WalkResult(), "facts": []}

    def inventory() -> dict[str, Any]:
        holder["walk"] = _walk(root, matcher, ctx)
        holder["facts"] = _scan_all(holder["walk"], ctx)
        return build_inventory(holder["walk"], holder["facts"], ctx, exclude)

    report["inventory"] = steps.run(
        "inventory", "Inventory of ARXML, DCF and DPA files", inventory, _no_section()
    )
    walk: WalkResult = holder["walk"]
    facts: list[ArxmlFacts] = holder["facts"]
    report["storage_map"] = steps.run(
        "storage_map", "Storage map", lambda: build_storage_map(facts, ctx), _no_section()
    )
    workspace = steps.run(
        "workspace",
        "Workspace index (.dcf and .dpa)",
        lambda: build_workspace(walk, root, ctx, _explicit_dpa(options.dpa, root, ctx)),
        WorkspaceResult(section={}),
    )
    report["workspace"] = workspace.section
    report["xml_style"] = steps.run(
        "xml_style", "XML style per folder", lambda: build_xml_style(facts, ctx), _no_section()
    )
    report["ecuc"] = steps.run(
        "ecuc", "ECUC structure (Rte, Os)", lambda: build_ecuc(facts, ctx), _no_section()
    )
    report["composition"] = steps.run(
        "composition", "Composition shape", lambda: build_composition(facts, ctx), _no_section()
    )
    owners = steps.run(
        "owners",
        "Model-owned and locked candidates",
        lambda: build_owners(facts, ctx),
        OwnersResult({}, "", "", "", ""),
    )
    report["owners"] = owners.section
    report["lock_signs"] = steps.run(
        "lock_signs",
        "Lock and temporary files",
        lambda: build_lock_signs(walk, ctx),
        _no_section(),
    )
    git = Git(root, options.git_executable)
    report["git"] = steps.run(
        "git", "Git state", lambda: build_git(git, root, walk, ctx, own_rel), _no_section()
    )

    def tools() -> ToolsResult:
        if options.no_tools:
            ctx.info("DISC-TOOLS-SKIPPED", "--no-tools was given: no Vector tool was started.")
            return ToolsResult(section=skipped_tools_section())
        search = ToolSearch(
            developer_bin=os.path.abspath(options.developer_bin) if options.developer_bin else None,
            sip=os.path.abspath(options.sip) if options.sip else None,
            dpa_paths=[
                item.target.replace("/", os.sep)
                for item in workspace.dpa_paths
                if item.status in ("file", "dir")
            ],
            repo_files=walk.files,
            suffixes=options.tool_suffixes,
            environ=environ,
        )
        return build_tools(
            search,
            ctx,
            repo_root=root,
            launcher=options.tool_launcher,
            timeout_s=options.tool_timeout_s,
        )

    tool_result = steps.run(
        "tools", "Vector command-line tools (help flags only)", tools, ToolsResult(section={})
    )
    report["tools"] = tool_result.section
    report["steps"] = steps.records
    report["step_errors"] = steps.errors
    report["seconds"] = round(time.monotonic() - started, 2)

    ctx.step = "write"
    ctx.progress("Writing the report ...")
    writer = _Writer(ctx, output.path)
    _clear_cli(output.path)
    for name, text in sorted(tool_result.cli_files.items()):
        writer.text("cli/" + name, text)
    if owners.section:
        writer.text("owners_proposed.csv", owners.owners_csv)
        writer.text("locked_proposed.txt", owners.locked_txt)
    if output.private is not None:
        _write_token_map(output.private, ctx.names, ctx)
        if owners.section:
            writer.text("owners_proposed.csv", owners.owners_csv_private, folder=output.private)
            writer.text("locked_proposed.txt", owners.locked_txt_private, folder=output.private)
    report["findings"] = ctx.findings_json()
    report["findings_by_severity"] = ctx.count_by_severity()
    writer.text("discovery.md", render.render_discovery_md(report))
    _finish(output, writer, report, "discovery.json", ctx)
    summary = render.render_summary(report, output.path, output.private)
    return DiscoveryResult(output.path, output.private, report, writer.written, summary)


# --------------------------------------------------------------------------- golden diff


def run_golden_diff(
    options: GoldenDiffOptions, progress: Progress | None = None
) -> DiscoveryResult:
    """Compare two commits and write ``golden_diff_<base>_<head>.json`` and ``.md``.

    Raises ConfigError, WriteError or GitError (git missing, not a repo, unknown commit).
    """
    started = time.monotonic()
    root = _check_repo(options.repo)
    git = Git(root, options.git_executable)
    probe = git.run("--version")
    if probe.spawn_error is not None:
        raise GitError(
            "git could not be started. Install Git for Windows or pass --git <path to git.exe>."
        )
    output = prepare_output(root, options.out, options.anonymize)
    ctx = Context(progress=progress or (lambda _text: None))
    if output.private is not None:
        ctx.names = Names(anonymize=True, key=_load_key(output.private, ctx))
    report = _header("golden-diff", _mode(options.anonymize, "golden-diff", False))
    ctx.step = "golden_diff"
    ctx.progress("[1/1] Comparing the two commits ...")
    report["golden_diff"] = build_golden_diff(
        git, GoldenOptions(base=options.base, head=options.head), ctx
    )
    report["seconds"] = round(time.monotonic() - started, 2)
    detail = report["golden_diff"]
    stem = f"golden_diff_{detail['base_commit'][:8]}_{detail['head_commit'][:8]}"

    ctx.step = "write"
    ctx.progress("Writing the report ...")
    writer = _Writer(ctx, output.path)
    if output.private is not None:
        _write_token_map(output.private, ctx.names, ctx)
    report["findings"] = ctx.findings_json()
    report["findings_by_severity"] = ctx.count_by_severity()
    writer.text(stem + ".md", render.render_golden_md(report))
    _finish(output, writer, report, stem + ".json", ctx)
    summary = render.render_golden_summary(report, output.path, output.private, stem)
    return DiscoveryResult(output.path, output.private, report, writer.written, summary)
