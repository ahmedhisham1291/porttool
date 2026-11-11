"""Step 10: find the Vector command-line tools and capture their help output.

Safety rule of this module: a Vector tool is started in exactly one place,
``help_command``, and only with one of ``HELP_FLAGS`` as its single argument. No
project, workspace or repo path is ever passed, and the working folder is a fresh
temporary folder outside the repo.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
import struct
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from porttool.discovery.context import Context
from porttool.discovery.fsio import FileEntry, io_path, iso_utc
from porttool.discovery.proc import ProcResult, decode_output, run_captured

DEVELOPER_TOOLS: tuple[str, ...] = ("DVImEx", "DVWspChecker", "DVSwcGen")
CONFIGURATOR_TOOL = "DVCfgCmd"
ALL_TOOLS: tuple[str, ...] = (*DEVELOPER_TOOLS, CONFIGURATOR_TOOL)

#: The only arguments a Vector tool is ever started with, tried one at a time.
HELP_FLAGS: tuple[str, ...] = ("--help", "-h", "-?", "/?", "help")
_FLAG_FILE_NAMES: dict[str, str] = {
    "--help": "dashdash-help",
    "-h": "dash-h",
    "-?": "dash-question",
    "/?": "slash-question",
    "help": "help",
}
HELP_TIMEOUT_S = 30.0
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_PROBED_PER_TOOL = 3
MAX_SEARCH_FOLDERS = 5000

#: PENDING-DISCOVERY: install locations. The spec names these; the report shows what
#: was really found so the defaults in porttool.toml can be set from facts.
PROGRAM_FILES_VARIABLES: tuple[str, ...] = ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432")
DEVELOPER_FOLDER_GLOB = "vector davinci developer*"
#: Where DVCfgCmd may sit relative to a folder named in the .dpa or to the SIP root.
CONFIGURATOR_SUBFOLDERS: tuple[str, ...] = ("", "Core", "DaVinciConfigurator/Core")

Launcher = Callable[[str], Sequence[str]]


def direct_launcher(path: str) -> Sequence[str]:
    """Start an executable directly. Tests replace this to run fake tool scripts."""
    return (path,)


def help_command(prefix: Sequence[str], flag: str) -> list[str]:
    """The only command line discovery ever builds for a Vector tool."""
    if flag not in HELP_FLAGS:
        raise ValueError(f"refusing to start a tool with the argument {flag!r}")
    return [*prefix, flag]


# --------------------------------------------------------------------------- file version


@dataclass(frozen=True, slots=True)
class PeVersion:
    file_version: str
    product_version: str
    strings: dict[str, str]


_FIXED_INFO_SIGNATURE = b"\xbd\x04\xef\xfe"
_VERSION_KEYS: tuple[str, ...] = (
    "CompanyName",
    "ProductName",
    "FileDescription",
    "FileVersion",
    "ProductVersion",
)
_MAX_VERSION_SCAN = 256 * 1024 * 1024


def _utf16_value_after(data: bytes, key: str, start: int) -> str | None:
    needle = key.encode("utf-16-le") + b"\x00\x00"
    position = data.find(needle, start)
    if position < 0:
        return None
    cursor = position + len(needle)
    while data[cursor : cursor + 2] == b"\x00\x00" and cursor < position + len(needle) + 6:
        cursor += 2
    end = cursor
    while end + 1 < len(data) and data[end : end + 2] != b"\x00\x00" and end - cursor < 512:
        end += 2
    text = data[cursor:end].decode("utf-16-le", errors="replace").strip()
    return text if text and text.isprintable() else None


def parse_pe_version(data: bytes) -> PeVersion | None:
    """Find the VS_FIXEDFILEINFO block in the bytes of an executable and read it."""
    position = data.find(_FIXED_INFO_SIGNATURE)
    while position >= 0:
        block = data[position : position + 24]
        if len(block) == 24:
            _, struct_version, file_ms, file_ls, product_ms, product_ls = struct.unpack(
                "<6I", block
            )
            if struct_version == 0x00010000:
                strings = {}
                for key in _VERSION_KEYS:
                    value = _utf16_value_after(data, key, position)
                    if value is not None:
                        strings[key] = value
                return PeVersion(
                    file_version=_dotted(file_ms, file_ls),
                    product_version=_dotted(product_ms, product_ls),
                    strings=strings,
                )
        position = data.find(_FIXED_INFO_SIGNATURE, position + 4)
    return None


def _dotted(most: int, least: int) -> str:
    return f"{most >> 16}.{most & 0xFFFF}.{least >> 16}.{least & 0xFFFF}"


def read_file_version(path: str) -> PeVersion | None:
    """Read the version resource of a Windows executable. None if it has none."""
    size = os.path.getsize(io_path(path))
    if size > _MAX_VERSION_SCAN:
        return None
    with open(io_path(path), "rb") as handle:
        return parse_pe_version(handle.read())


# --------------------------------------------------------------------------- search


@dataclass(frozen=True, slots=True)
class ToolExecutable:
    tool: str
    path: str
    found_via: str
    size: int
    mtime_ns: int


@dataclass(slots=True)
class ToolSearch:
    developer_bin: str | None = None
    sip: str | None = None
    dpa_paths: Sequence[str] = ()  # absolute paths named in the .dpa that exist
    repo_files: Sequence[FileEntry] = ()
    suffixes: tuple[str, ...] = (".exe",)
    environ: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    unreadable_folders: int = 0
    developer_folders: list[str] = field(default_factory=list)


def tool_of(file_name: str, suffixes: Sequence[str]) -> str | None:
    """Name of the Vector tool a file name belongs to, compared without case."""
    lower = file_name.lower()
    for tool in ALL_TOOLS:
        for suffix in suffixes:
            if lower == tool.lower() + suffix.lower():
                return tool
    return None


def _scan_tree(root: str, depth: int, search: ToolSearch) -> list[tuple[str, str]]:
    """(tool, path) for every tool file at most ``depth`` folders below ``root``."""
    found: list[tuple[str, str]] = []
    pending: list[tuple[str, int]] = [(root, 0)]
    visited = 0
    while pending and visited < MAX_SEARCH_FOLDERS:
        folder, level = pending.pop(0)
        visited += 1
        try:
            with os.scandir(io_path(folder)) as scan:
                entries = sorted(scan, key=lambda entry: entry.name.lower())
        except OSError:
            search.unreadable_folders += 1
            continue
        for entry in entries:
            path = os.path.join(folder, entry.name)
            try:
                if entry.is_dir(follow_symlinks=False):
                    if level < depth:
                        pending.append((path, level + 1))
                    continue
                is_file = entry.is_file()
            except OSError:
                search.unreadable_folders += 1
                continue
            tool = tool_of(entry.name, search.suffixes)
            if tool is not None and is_file:
                found.append((tool, path))
    return found


def _program_files_folders(search: ToolSearch) -> list[str]:
    folders: list[str] = []
    seen: set[str] = set()
    for variable in PROGRAM_FILES_VARIABLES:
        base = search.environ.get(variable, "")
        if not base or base.casefold() in seen:
            continue
        seen.add(base.casefold())
        try:
            with os.scandir(io_path(base)) as scan:
                names = sorted(entry.name for entry in scan if entry.is_dir())
        except OSError:
            search.unreadable_folders += 1
            continue
        for name in names:
            if fnmatch.fnmatchcase(name.lower(), DEVELOPER_FOLDER_GLOB):
                folders.append(os.path.join(base, name))
    return folders


def find_executables(search: ToolSearch) -> list[ToolExecutable]:
    """Look for the four tools in the order the options, the machine and the repo give."""
    candidates: list[tuple[str, str, str]] = []  # tool, path, found via
    if search.developer_bin:
        for tool, path in _scan_tree(search.developer_bin, 2, search):
            candidates.append((tool, path, "--developer-bin"))
    search.developer_folders = _program_files_folders(search)
    for folder in search.developer_folders:
        for tool, path in _scan_tree(folder, 3, search):
            candidates.append((tool, path, "Program Files"))
    if search.sip:
        for tool, path in _scan_tree(search.sip, 4, search):
            candidates.append((tool, path, "--sip"))
    for named in search.dpa_paths:
        start = named if os.path.isdir(io_path(named)) else os.path.dirname(named)
        folder = start
        for _ in range(4):
            for sub in CONFIGURATOR_SUBFOLDERS:
                base = os.path.join(folder, *sub.split("/")) if sub else folder
                for suffix in search.suffixes:
                    path = os.path.join(base, CONFIGURATOR_TOOL + suffix)
                    if os.path.isfile(io_path(path)):
                        candidates.append((CONFIGURATOR_TOOL, path, "path in the .dpa file"))
            parent = os.path.dirname(folder)
            if parent == folder:
                break
            folder = parent
    for entry in search.repo_files:
        tool = tool_of(entry.name, search.suffixes)
        if tool is not None:
            candidates.append((tool, entry.path, "inside the repo"))

    found: list[ToolExecutable] = []
    seen: set[str] = set()
    for tool, path, via in candidates:
        key = os.path.normcase(os.path.abspath(path)).casefold()
        if key in seen:
            continue
        seen.add(key)
        try:
            stat = os.stat(io_path(path))
        except OSError:
            search.unreadable_folders += 1
            continue
        found.append(ToolExecutable(tool, path, via, stat.st_size, stat.st_mtime_ns))
    return found


# --------------------------------------------------------------------------- probing


@dataclass(frozen=True, slots=True)
class HelpAttempt:
    flag: str
    result: ProcResult
    files_created: tuple[str, ...]
    text: str  # decoded stdout and stderr with a header: the content of the cli file
    decoding: str
    looks_like_help: bool


_HELP_HINT_RE = re.compile(r"usage|options?\b|syntax|--?[a-z][\w-]*|/[a-z?]\b", re.IGNORECASE)


def flag_file_name(tool_label: str, flag: str) -> str:
    return f"{tool_label}_{_FLAG_FILE_NAMES[flag]}.txt"


def _attempt_text(
    tool: ToolExecutable, shown_path: str, flag: str, result: ProcResult, created: Sequence[str]
) -> tuple[str, str, bool]:
    out_text, out_decoding = decode_output(result.stdout)
    err_text, err_decoding = decode_output(result.stderr)
    decoding = out_decoding if result.stdout else err_decoding
    if result.spawn_error is not None:
        outcome = f"did not start ({result.spawn_error})"
    elif result.timed_out:
        outcome = (
            f"no exit within {result.duration_s:.0f} s; killed {result.killed} process(es), "
            f"{result.survivors} still alive"
        )
    else:
        outcome = f"exit code {result.exit_code}"
    lines = [
        "# porttool discover: output of a Vector tool started with one help flag",
        f"# tool: {tool.tool}",
        f"# executable: {shown_path}",
        f"# argument: {flag}",
        f"# result: {outcome}",
        f"# duration: {result.duration_s:.2f} s",
        f"# stdout: {len(result.stdout)} bytes ({out_decoding}), "
        f"stderr: {len(result.stderr)} bytes ({err_decoding})",
        f"# output cut at {MAX_OUTPUT_BYTES} bytes: {'yes' if result.truncated else 'no'}",
        f"# files the tool created in its temporary working folder: {len(created)}",
        "==================== stdout ====================",
        out_text.rstrip("\r\n"),
        "==================== stderr ====================",
        err_text.rstrip("\r\n"),
        "",
    ]
    combined = out_text + "\n" + err_text
    looks = (
        result.started
        and not result.timed_out
        and len(combined.strip().splitlines()) >= 3
        and _HELP_HINT_RE.search(combined) is not None
    )
    return "\n".join(lines), decoding, looks


def probe_executable(
    tool: ToolExecutable,
    shown_path: str,
    launcher: Launcher,
    timeout_s: float,
    progress: Callable[[str], None],
) -> list[HelpAttempt]:
    """Start one executable once per help flag and capture what it prints."""
    attempts: list[HelpAttempt] = []
    prefix = list(launcher(tool.path))
    for flag in HELP_FLAGS:
        workdir = tempfile.mkdtemp(prefix="porttool_discover_")
        try:
            result = run_captured(
                help_command(prefix, flag),
                timeout_s=timeout_s,
                cwd=workdir,
                max_bytes=MAX_OUTPUT_BYTES,
            )
            try:
                created = tuple(sorted(os.listdir(workdir)))
            except OSError:
                created = ()
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        text, decoding, looks = _attempt_text(tool, shown_path, flag, result, created)
        attempts.append(HelpAttempt(flag, result, created, text, decoding, looks))
        if result.spawn_error is not None:
            state = f"did not start ({result.spawn_error})"
        elif result.timed_out:
            state = f"no answer after {timeout_s:.0f} s, process tree killed"
        else:
            state = f"exit code {result.exit_code}"
        progress(f"      {tool.tool} {flag}: {state}")
    return attempts


# --------------------------------------------------------------------------- step


@dataclass(slots=True)
class ToolsResult:
    section: dict[str, Any]
    cli_files: dict[str, str] = field(default_factory=dict)  # file name -> text


def _redactions(repo_root: str, environ: Mapping[str, str]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for value, label in (
        (repo_root, "<REPO>"),
        (repo_root.replace("\\", "/"), "<REPO>"),
        (environ.get("USERPROFILE", ""), "<HOME>"),
        (environ.get("HOME", ""), "<HOME>"),
        (environ.get("COMPUTERNAME", ""), "<COMPUTER>"),
        (environ.get("USERNAME", ""), "<USER>"),
        (environ.get("USER", ""), "<USER>"),
    ):
        if len(value) >= 3 and (value, label) not in pairs:
            pairs.append((value, label))
    return pairs


def redact(text: str, pairs: Sequence[tuple[str, str]]) -> str:
    """Replace machine-specific strings (repo path, user, computer) without regard to case."""
    for needle, label in pairs:
        text = re.sub(re.escape(needle), lambda _m, shown=label: shown, text, flags=re.IGNORECASE)
    return text


def build_tools(
    search: ToolSearch,
    ctx: Context,
    *,
    repo_root: str,
    launcher: Launcher = direct_launcher,
    timeout_s: float = HELP_TIMEOUT_S,
) -> ToolsResult:
    names = ctx.names
    for option, folder in (("--developer-bin", search.developer_bin), ("--sip", search.sip)):
        if folder is not None and not os.path.isdir(io_path(folder)):
            ctx.warning(
                "DISC-TOOL-FOLDER",
                f"The folder given with {option} does not exist or is not a folder.",
            )
    executables = find_executables(search)
    pairs = _redactions(repo_root, search.environ) if names.anonymized else []
    result = ToolsResult(section={})
    listed: list[dict[str, Any]] = []
    probed: dict[str, int] = {}
    for executable in executables:
        number = probed.get(executable.tool, 0) + 1
        probed[executable.tool] = number
        label = executable.tool if number == 1 else f"{executable.tool}-{number}"
        shown_path = names.tool_path(executable.path)
        version = None
        try:
            version = read_file_version(executable.path)
        except OSError as error:
            ctx.warning(
                "DISC-TOOL-VERSION",
                f"The file version of {executable.tool} could not be read "
                f"({type(error).__name__}).",
            )
        item: dict[str, Any] = {
            "tool": executable.tool,
            "path": shown_path,
            "found_via": executable.found_via,
            "bytes": executable.size,
            "modified_utc": iso_utc(executable.mtime_ns),
            "file_version": version.file_version if version else None,
            "product_version": version.product_version if version else None,
            "version_strings": version.strings if version else {},
            "attempts": [],
        }
        listed.append(item)
        if number > MAX_PROBED_PER_TOOL:
            item["not_started"] = f"only the first {MAX_PROBED_PER_TOOL} copies are started"
            continue
        ctx.progress(f"    {executable.tool}: {len(HELP_FLAGS)} help flags, one at a time")
        for attempt in probe_executable(executable, shown_path, launcher, timeout_s, ctx.progress):
            file_name = flag_file_name(label, attempt.flag)
            result.cli_files[file_name] = redact(attempt.text, pairs)
            run = attempt.result
            item["attempts"].append(
                {
                    "flag": attempt.flag,
                    "exit_code": run.exit_code,
                    "timed_out": run.timed_out,
                    "spawn_error": run.spawn_error,
                    "duration_s": round(run.duration_s, 3),
                    "stdout_bytes": len(run.stdout),
                    "stderr_bytes": len(run.stderr),
                    "decoding": attempt.decoding,
                    "output_cut": run.truncated,
                    "processes_killed": run.killed,
                    "processes_still_alive": run.survivors,
                    "files_created_in_temporary_folder": len(attempt.files_created),
                    "looks_like_help": attempt.looks_like_help,
                    "output_file": "cli/" + file_name,
                }
            )
            if run.spawn_error is not None:
                ctx.warning(
                    "DISC-TOOL-SPAWN",
                    f"{executable.tool} could not be started with {attempt.flag} "
                    f"({run.spawn_error}).",
                )
            elif run.timed_out:
                ctx.warning(
                    "DISC-TOOL-TIMEOUT",
                    f"{executable.tool} did not exit within {timeout_s:.0f} s when started "
                    f"with {attempt.flag}; {run.killed} process(es) were killed"
                    + (f", {run.survivors} could not be killed." if run.survivors else "."),
                )
            elif run.exit_code != 0:
                ctx.info(
                    "DISC-TOOL-EXIT",
                    f"{executable.tool} returned exit code {run.exit_code} for {attempt.flag}.",
                )
    missing = [tool for tool in ALL_TOOLS if tool not in probed]
    for tool in missing:
        ctx.warning(
            "DISC-TOOL-NOT-FOUND",
            f"{tool} was not found. Pass "
            + ("--sip <SIP folder>" if tool == CONFIGURATOR_TOOL else "--developer-bin <folder>")
            + " and run the command again.",
        )
    result.section = {
        "skipped": False,
        "help_flags": list(HELP_FLAGS),
        "timeout_s": timeout_s,
        "search": {
            "developer_bin_given": search.developer_bin is not None,
            "sip_given": search.sip is not None,
            "program_files_variables_set": [
                variable for variable in PROGRAM_FILES_VARIABLES if search.environ.get(variable)
            ],
            "developer_folders_found": [
                names.tool_path(folder) for folder in search.developer_folders
            ],
            "paths_from_dpa_checked": len(search.dpa_paths),
            "unreadable_folders": search.unreadable_folders,
        },
        "executables": listed,
        "not_found": missing,
    }
    return result


def skipped_tools_section() -> dict[str, Any]:
    return {
        "skipped": True,
        "help_flags": list(HELP_FLAGS),
        "executables": [],
        "not_found": [],
        "note": "--no-tools was given: no tool was searched and none was started.",
    }
