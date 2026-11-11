"""File discovery: which files does the reader load for a ``ProjectConfig``?

Loaded: every ``*.arxml`` under ``config.arxml_roots`` that no exclude pattern matches,
plus ``config.ecuc_file``. Never loaded from the roots: ``config.gen_data``,
``config.sip_root``, ``config.flat_extract``, ``config.flat_map`` and version-control
folders. If ``config.dcf_file`` is set, the files it lists are compared with the files
found on disk and every difference becomes a finding.

Exclude pattern semantics (``config.exclude_globs``), matched without regard to letter
case against the repo-relative path with forward slashes:

* ``*`` matches inside one path segment, ``?`` one character, ``[abc]`` a character set;
* ``**`` as a whole segment matches any number of folders, including none;
* a pattern without ``/`` matches a file or folder name at any depth;
* a pattern that matches a folder excludes everything below it.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from porttool.core.config import ProjectConfig
from porttool.core.models import SourceFile
from porttool.core.results import Finding, Severity
from porttool.reader import codes
from porttool.reader.dcf import DcfFormatError, list_arxml_references

_ARXML_SUFFIX = ".arxml"
_SKIPPED_FOLDERS = frozenset({".git", ".svn", ".hg"})


@dataclass(frozen=True, slots=True)
class DiscoveredFile:
    abs_path: str
    rel_path: str  # repo-relative with forward slashes (absolute if outside the repo)
    size: int
    mtime_ns: int
    is_ecuc: bool = False
    in_roots: bool = True


@dataclass(frozen=True, slots=True)
class Discovery:
    files: tuple[DiscoveredFile, ...]  # sorted by rel_path
    extra: tuple[SourceFile, ...]  # fingerprinted but not parsed as ARXML: the .dcf
    findings: tuple[Finding, ...]

    def fingerprints(self) -> dict[str, tuple[int, int]]:
        result = {f.rel_path: (f.size, f.mtime_ns) for f in self.files}
        result.update({f.rel_path: (f.size, f.mtime_ns) for f in self.extra})
        return result


# --------------------------------------------------------------------------- globs


def _segment_regex(segment: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(segment):
        ch = segment[i]
        if ch == "*":
            while i + 1 < len(segment) and segment[i + 1] == "*":
                i += 1
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        elif ch == "[":
            end = segment.find("]", i + 2)
            if end == -1:
                out.append(re.escape(ch))
            else:
                body = segment[i + 1 : end]
                negate = body[:1] in ("!", "^")
                if negate:
                    body = body[1:]
                body = body.replace("\\", "\\\\")
                out.append(f"[{'^' if negate else ''}{body}]")
                i = end
        else:
            out.append(re.escape(ch))
        i += 1
    return "".join(out)


def glob_to_regex(pattern: str) -> str:
    """Translate one exclude pattern to a regular expression (see the module docstring)."""
    text = pattern.strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    text = text.strip("/")
    while text.endswith("/**"):
        text = text[:-3].rstrip("/")
    if text in ("", "**"):
        return ".*"
    if "/" not in text:
        text = "**/" + text
    parts: list[str] = []
    segments = text.split("/")
    for index, segment in enumerate(segments):
        last = index == len(segments) - 1
        if segment == "**":
            parts.append("(?:[^/]+/)*" if not last else "[^/]+")
        else:
            parts.append(_segment_regex(segment) + ("" if last else "/"))
    return "".join(parts) + "(?:/.*)?"


def compile_globs(patterns: Iterable[str]) -> Callable[[str], bool]:
    """Return ``matches(rel_path)`` for a set of exclude patterns."""
    regexes = [glob_to_regex(p) for p in patterns if p.strip()]
    if not regexes:
        return lambda _rel_path: False
    compiled = re.compile("|".join(f"(?:{r})" for r in regexes), re.IGNORECASE)
    return lambda rel_path: compiled.fullmatch(rel_path) is not None


# --------------------------------------------------------------------------- walking


def _norm(path: str | Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def relative_path(path: str | Path, repo_root: str | Path) -> str:
    """Repo-relative path with forward slashes; the absolute path if outside the repo."""
    absolute = os.path.abspath(path)
    try:
        rel = os.path.relpath(absolute, os.path.abspath(repo_root))
    except ValueError:  # another drive
        return absolute.replace("\\", "/")
    if rel == os.pardir or rel.startswith(os.pardir + os.sep):
        return absolute.replace("\\", "/")
    return "" if rel == os.curdir else rel.replace("\\", "/")


def _is_arxml(name: str) -> bool:
    return name.lower().endswith(_ARXML_SUFFIX)


class _Scan:
    def __init__(self, config: ProjectConfig) -> None:
        self.config = config
        self.repo_root = os.path.abspath(config.repo_root)
        self.excluded = compile_globs(config.exclude_globs)
        self.never_folders = {_norm(p) for p in (config.gen_data, config.sip_root) if p is not None}
        self.never_files = {
            _norm(p) for p in (config.flat_extract, config.flat_map) if p is not None
        }
        self.files: dict[str, DiscoveredFile] = {}  # by normalised absolute path
        self.findings: list[Finding] = []

    def add_file(self, abs_path: str, rel_path: str, *, is_ecuc: bool, in_roots: bool) -> None:
        key = _norm(abs_path)
        known = self.files.get(key)
        if known is not None:
            if (is_ecuc and not known.is_ecuc) or (in_roots and not known.in_roots):
                self.files[key] = DiscoveredFile(
                    known.abs_path,
                    known.rel_path,
                    known.size,
                    known.mtime_ns,
                    is_ecuc=known.is_ecuc or is_ecuc,
                    in_roots=known.in_roots or in_roots,
                )
            return
        try:
            info = os.stat(abs_path)
        except OSError:
            return  # gone between listing and stat: it is simply not there
        self.files[key] = DiscoveredFile(
            abs_path, rel_path, info.st_size, info.st_mtime_ns, is_ecuc=is_ecuc, in_roots=in_roots
        )

    def walk(self, root: str, *, is_ecuc: bool, in_roots: bool, filtered: bool) -> None:
        stack = [(root, relative_path(root, self.repo_root))]
        while stack:
            folder, folder_rel = stack.pop()
            try:
                with os.scandir(folder) as iterator:
                    entries = list(iterator)
            except OSError as error:
                self.findings.append(
                    Finding(
                        code=codes.FOLDER_UNREADABLE,
                        severity=Severity.ERROR,
                        message=f"The folder could not be listed: {error.strerror or error}.",
                        file=folder_rel or ".",
                    )
                )
                continue
            for entry in entries:
                name = entry.name
                rel = f"{folder_rel}/{name}" if folder_rel else name
                try:
                    is_folder = entry.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if is_folder:
                    if name in _SKIPPED_FOLDERS:
                        continue
                    if filtered and (self.excluded(rel) or _norm(entry.path) in self.never_folders):
                        continue
                    stack.append((entry.path, rel))
                elif _is_arxml(name):
                    if filtered and (self.excluded(rel) or _norm(entry.path) in self.never_files):
                        continue
                    self.add_file(entry.path, rel, is_ecuc=is_ecuc, in_roots=in_roots)

    def add_root(self, root: Path) -> None:
        path = os.path.abspath(root)
        rel = relative_path(path, self.repo_root)
        if os.path.isdir(path):
            if self.excluded(rel) or _norm(path) in self.never_folders:
                return
            self.walk(path, is_ecuc=False, in_roots=True, filtered=True)
        elif os.path.isfile(path):
            if _is_arxml(path) and not self.excluded(rel) and _norm(path) not in self.never_files:
                self.add_file(path, rel, is_ecuc=False, in_roots=True)
        else:
            self.findings.append(
                Finding(
                    code=codes.ROOT_MISSING,
                    severity=Severity.ERROR,
                    message="The configured ARXML root does not exist.",
                    file=rel,
                )
            )

    def add_ecuc(self, ecuc: Path) -> None:
        path = os.path.abspath(ecuc)
        rel = relative_path(path, self.repo_root)
        if os.path.isfile(path):
            self.add_file(path, rel, is_ecuc=True, in_roots=False)
        elif os.path.isdir(path):
            # PENDING-DISCOVERY: DaVinci Configurator can split the ECUC over one file per
            # module. ProjectConfig.ecuc_file is a single path, so a folder is accepted
            # here and every ARXML file below it is read as ECUC.
            self.walk(path, is_ecuc=True, in_roots=False, filtered=False)
        else:
            self.findings.append(
                Finding(
                    code=codes.ROOT_MISSING,
                    severity=Severity.ERROR,
                    message="The configured ECUC file does not exist. "
                    "Tasks and task mappings are missing from the model.",
                    file=rel,
                )
            )


# --------------------------------------------------------------------------- .dcf


def _compare_with_dcf(scan: _Scan, dcf_file: Path, dcf_rel: str) -> None:
    try:
        listed = list_arxml_references(dcf_file)
    except (OSError, DcfFormatError) as error:
        scan.findings.append(
            Finding(
                code=codes.DCF_UNREADABLE,
                severity=Severity.ERROR,
                message="The DaVinci workspace file could not be read, so the files on disk "
                f"were not compared with it: {error}",
                file=dcf_rel,
            )
        )
        return
    folder = os.path.dirname(os.path.abspath(dcf_file))
    listed_keys: set[str] = set()
    for reference in listed:
        absolute = os.path.normpath(os.path.join(folder, reference))
        key = _norm(absolute)
        listed_keys.add(key)
        if key in scan.files:
            continue
        rel = relative_path(absolute, scan.repo_root)
        if os.path.isfile(absolute):
            scan.findings.append(
                Finding(
                    code=codes.DCF_NOT_LOADED,
                    severity=Severity.WARNING,
                    message="The file is listed in the DaVinci workspace file but is not "
                    "loaded: it is outside the configured ARXML roots or excluded.",
                    file=rel,
                )
            )
        else:
            scan.findings.append(
                Finding(
                    code=codes.DCF_MISSING_ON_DISK,
                    severity=Severity.ERROR,
                    message="The file is listed in the DaVinci workspace file but is "
                    "missing on disk.",
                    file=rel,
                )
            )
    for key, found in sorted(scan.files.items(), key=lambda item: item[1].rel_path):
        if found.in_roots and not found.is_ecuc and key not in listed_keys:
            scan.findings.append(
                Finding(
                    code=codes.DCF_NOT_LISTED,
                    severity=Severity.WARNING,
                    message="The file is on disk under an ARXML root but the DaVinci "
                    "workspace file does not list it.",
                    file=found.rel_path,
                )
            )


# --------------------------------------------------------------------------- entry point


def discover(config: ProjectConfig, *, compare_dcf: bool = True) -> Discovery:
    """Scan the disk. Blocking: call it through ``JobContext.run_blocking``.

    ``compare_dcf=False`` skips reading the .dcf content (used by the staleness check);
    the .dcf itself is still fingerprinted.
    """
    scan = _Scan(config)
    for root in config.arxml_roots:
        scan.add_root(root)
    if config.ecuc_file is not None:
        scan.add_ecuc(config.ecuc_file)
    extra: list[SourceFile] = []
    if config.dcf_file is not None:
        dcf_rel = relative_path(config.dcf_file, scan.repo_root)
        try:
            info = os.stat(config.dcf_file)
        except OSError as error:
            scan.findings.append(
                Finding(
                    code=codes.DCF_UNREADABLE,
                    severity=Severity.ERROR,
                    message="The DaVinci workspace file could not be read: "
                    f"{error.strerror or error}.",
                    file=dcf_rel,
                )
            )
        else:
            extra.append(SourceFile(rel_path=dcf_rel, size=info.st_size, mtime_ns=info.st_mtime_ns))
            if compare_dcf:
                _compare_with_dcf(scan, config.dcf_file, dcf_rel)
    files = tuple(sorted(scan.files.values(), key=lambda f: f.rel_path))
    return Discovery(files=files, extra=tuple(extra), findings=tuple(scan.findings))
