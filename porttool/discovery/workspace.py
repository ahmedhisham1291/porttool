"""Step 3: learn the format of the workspace index files (.dcf and .dpa).

Nobody has shown us these files, so nothing here assumes a tag name. The file is read
as generic XML and summarised as a tree of tags with attribute names, occurrence counts
and up to three sample values per leaf. Every value that looks like a file or folder
path is resolved against the file's own folder and compared with what is on disk.
"""

from __future__ import annotations

import os
import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from porttool.discovery.context import Context, top
from porttool.discovery.fsio import FileEntry, WalkResult, io_path, read_chunks
from porttool.discovery.names import Names, has_variable, looks_like_path, split_extension
from porttool.discovery.xmlfeed import XmlProblem, XmlSink, feed_xml
from porttool.discovery.xmlstyle import StyleCollector

MAX_SAMPLES = 3
MAX_SAMPLE_CHARS = 200
MAX_DISTINCT = 50
MAX_PATH_VALUES = 200_000
MAX_ITEMS = 200
MAX_FILES_PER_TYPE = 10
_FALLBACK_BYTES = 4 * 1024 * 1024
_TOKEN_RE = re.compile(r"""[^\s"'<>=,;|]+""")
_WORD_RE = re.compile(r"[A-Za-z0-9_]+")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


# --------------------------------------------------------------------------- paths


def normalize_path(text: str) -> str:
    """Collapse separators, ``.`` and ``..`` into a forward-slash path (pure text logic)."""
    unified = text.replace("\\", "/")
    prefix = ""
    if unified.startswith("//"):
        prefix, unified = "//", unified.lstrip("/")
    elif unified.startswith("/"):
        prefix = "/"
    elif _DRIVE_RE.match(unified):
        prefix, unified = unified[:2] + "/", unified[2:]
    out: list[str] = []
    for segment in unified.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if out and out[-1] != "..":
                out.pop()
            elif not prefix:
                out.append("..")
        else:
            out.append(segment)
    return prefix + "/".join(out)


def is_absolute(text: str) -> bool:
    return text.startswith(("/", "\\")) or _DRIVE_RE.match(text) is not None


class DiskIndex:
    """What the walk found, looked up without regard to case (as Windows does)."""

    def __init__(self, repo_root: str, walk: WalkResult) -> None:
        self.root = normalize_path(repo_root)
        self._root_key = self.root.casefold()
        self.files: dict[str, str] = {entry.rel.casefold(): entry.rel for entry in walk.files}
        self.dirs: dict[str, str] = {rel.casefold(): rel for rel in walk.dirs}
        self.arxml: list[str] = [
            entry.rel for entry in walk.files if entry.name.lower().endswith(".arxml")
        ]

    def relative(self, absolute: str) -> str | None:
        """Repo-relative form of a normalized absolute path, or None if outside the repo."""
        key = absolute.casefold()
        if key == self._root_key:
            return ""
        if key.startswith(self._root_key + "/"):
            return absolute[len(self.root) + 1 :]
        return None

    def status(self, absolute: str) -> tuple[str, str | None]:
        """("file" | "dir" | "missing", repo-relative path as found on disk or None)."""
        rel = self.relative(absolute)
        if rel is not None:
            if rel == "":
                return "dir", ""
            key = rel.casefold()
            if key in self.files:
                return "file", self.files[key]
            if key in self.dirs:
                return "dir", self.dirs[key]
        native = io_path(absolute.replace("/", os.sep))
        if os.path.isdir(native):
            return "dir", rel
        if os.path.isfile(native):
            return "file", rel
        return "missing", rel


@dataclass(frozen=True, slots=True)
class ResolvedPath:
    location: str  # tag path plus "@attribute" or "#text"
    raw: str
    status: str  # "file", "dir", "missing", "variable"
    absolute: bool
    target: str  # normalized absolute path ("" when the value holds a variable)
    rel: str | None  # repo-relative path, None when outside the repo


def resolve_value(location: str, raw: str, base_folder: str, index: DiskIndex) -> ResolvedPath:
    """Resolve one path value against ``base_folder`` (a normalized absolute path)."""
    text = raw.strip()
    absolute = is_absolute(text)
    if has_variable(text):
        return ResolvedPath(location, text, "variable", absolute, "", None)
    target = normalize_path(text if absolute else base_folder + "/" + text)
    status, rel = index.status(target)
    return ResolvedPath(location, text, status, absolute, target, rel)


# --------------------------------------------------------------------------- structure


@dataclass(slots=True)
class LeafSummary:
    count: int = 0
    samples: list[str] = field(default_factory=list)
    distinct: set[str] = field(default_factory=set)

    def add(self, value: str) -> None:
        self.count += 1
        if value in self.distinct:
            return
        if len(self.distinct) < MAX_DISTINCT:
            self.distinct.add(value)
        if len(self.samples) < MAX_SAMPLES:
            self.samples.append(value)

    def as_dict(self, names: Names) -> dict[str, Any]:
        distinct = len(self.distinct)
        return {
            "count": self.count,
            "distinct": f"{MAX_DISTINCT}+" if distinct >= MAX_DISTINCT else str(distinct),
            "samples": [_shorten(names.value(sample)) for sample in self.samples],
        }


def _shorten(text: str) -> str:
    return text if len(text) <= MAX_SAMPLE_CHARS else text[:MAX_SAMPLE_CHARS] + "..."


@dataclass(slots=True)
class StructNode:
    tag: str
    count: int = 0
    attributes: dict[str, LeafSummary] = field(default_factory=dict)
    text: LeafSummary = field(default_factory=LeafSummary)
    children: dict[str, StructNode] = field(default_factory=dict)

    def as_dict(self, names: Names) -> dict[str, Any]:
        return {
            "tag": self.tag,
            "count": self.count,
            "attributes": {
                name: leaf.as_dict(names) for name, leaf in self.attributes.items()
            },
            "text": self.text.as_dict(names) if self.text.count else None,
            "children": [child.as_dict(names) for child in self.children.values()],
        }


class _StructureSink(XmlSink):
    def __init__(self) -> None:
        super().__init__()
        self.document = StructNode("#document")
        self.elements = 0
        self.comments = 0
        self.paths: list[tuple[str, str]] = []
        self._nodes: list[StructNode] = [self.document]
        self._texts: list[list[str]] = [[]]

    def _location(self, suffix: str) -> str:
        return "/" + "/".join(node.tag for node in self._nodes[1:]) + suffix

    def _path_value(self, suffix: str, value: str) -> None:
        if len(self.paths) < MAX_PATH_VALUES and looks_like_path(value):
            self.paths.append((self._location(suffix), value.strip()))

    def start(self, name: str, attrs: list[str]) -> None:
        self.elements += 1
        parent = self._nodes[-1]
        node = parent.children.get(name)
        if node is None:
            node = parent.children[name] = StructNode(name)
        node.count += 1
        self._nodes.append(node)
        self._texts.append([])
        for index in range(0, len(attrs), 2):
            key, value = attrs[index], attrs[index + 1]
            leaf = node.attributes.get(key)
            if leaf is None:
                leaf = node.attributes[key] = LeafSummary()
            leaf.add(value)
            self._path_value("@" + key, value)

    def data(self, text: str) -> None:
        self._texts[-1].append(text)

    def end(self, name: str) -> None:
        text = "".join(self._texts.pop()).strip()
        if text:
            self._nodes[-1].text.add(text)
            self._path_value("#text", text)
        self._nodes.pop()

    def comment(self, text: str) -> None:
        self.comments += 1


def text_fallback(text: str, names: Names) -> tuple[dict[str, Any], list[tuple[str, str]]]:
    """Describe a file that is not well-formed XML by the shapes of its lines."""
    lines = text.splitlines()
    shapes: Counter[str] = Counter()
    samples: dict[str, list[str]] = defaultdict(list)
    paths: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        shape = _WORD_RE.sub("w", stripped)[:80]
        shapes[shape] += 1
        if not names.anonymized and len(samples[shape]) < 2:
            samples[shape].append(_shorten(stripped))
        for token in _TOKEN_RE.findall(stripped):
            if token not in seen and looks_like_path(token):
                seen.add(token)
                paths.append(("#text-scan", token))
    summary = {
        "lines": len(lines),
        "line_shapes": [
            {"shape": shape, "lines": count, "samples": samples.get(shape, [])}
            for shape, count in shapes.most_common(15)
        ],
        "note": "In a shape, every run of letters, digits and underscores is written as w.",
    }
    return summary, paths


# --------------------------------------------------------------------------- one file


@dataclass(slots=True)
class WorkspaceFileResult:
    section: dict[str, Any]
    resolved: list[ResolvedPath]


def _read_head(path: str, limit: int) -> bytes:
    with open(io_path(path), "rb") as handle:
        return handle.read(limit)


def _separator_kind(raw: str) -> str:
    backslash, slash = "\\" in raw, "/" in raw
    if backslash and slash:
        return "mixed"
    if backslash:
        return "backslash"
    return "slash" if slash else "none"


def _paths_section(
    resolved: Sequence[ResolvedPath],
    relative_values: Sequence[tuple[str, str]],
    index: DiskIndex,
    names: Names,
) -> dict[str, Any]:
    by_location: dict[str, list[ResolvedPath]] = defaultdict(list)
    for item in resolved:
        by_location[item.location].append(item)
    locations: list[dict[str, Any]] = []
    for location, items in sorted(by_location.items(), key=lambda pair: (-len(pair[1]), pair[0])):
        extensions: Counter[str] = Counter()
        for item in items:
            extension = split_extension(item.raw.replace("\\", "/").rpartition("/")[2])[1]
            extensions[extension.lower() or "(none)"] += 1
        status = Counter(item.status for item in items)
        locations.append(
            {
                "location": location,
                "count": len(items),
                "existing_files": status["file"],
                "existing_folders": status["dir"],
                "missing": status["missing"],
                "with_variables": status["variable"],
                "absolute": sum(1 for item in items if item.absolute),
                "outside_repo": sum(
                    1 for item in items if item.rel is None and item.status != "variable"
                ),
                "extensions": top(extensions, 10),
                "samples": [_shorten(names.fs_path(item.raw)) for item in items[:MAX_SAMPLES]],
            }
        )
    against_root = Counter(
        resolve_value(location, raw, index.root, index).status for location, raw in relative_values
    )
    against_file = Counter(item.status for item in resolved if not item.absolute)
    return {
        "values_that_look_like_paths": len(resolved),
        "relative": sum(1 for item in resolved if not item.absolute),
        "absolute": sum(1 for item in resolved if item.absolute),
        "with_variables": sum(1 for item in resolved if item.status == "variable"),
        "separators": top(Counter(_separator_kind(item.raw) for item in resolved)),
        "relative_resolved_against_the_files_folder": {
            "existing": against_file["file"] + against_file["dir"],
            "missing": against_file["missing"],
        },
        "relative_resolved_against_the_repo_root": {
            "existing": against_root["file"] + against_root["dir"],
            "missing": against_root["missing"],
        },
        "by_location": locations,
    }


def _arxml_section(
    entry: FileEntry, resolved: Sequence[ResolvedPath], index: DiskIndex, names: Names
) -> dict[str, Any]:
    listed = [item for item in resolved if item.raw.lower().endswith(".arxml")]
    listed_files: set[str] = set()
    missing: list[str] = []
    mismatches: list[dict[str, str]] = []
    for item in listed:
        if item.status == "file" and item.rel is not None:
            listed_files.add(item.rel.casefold())
            written = index.relative(item.target)
            if written is not None and written != item.rel:
                mismatches.append(
                    {"listed": names.rel_path(written), "on_disk": names.rel_path(item.rel)}
                )
        elif item.status == "missing":
            missing.append(item.raw)
    listed_dirs = sorted(
        {item.rel for item in resolved if item.status == "dir" and item.rel is not None}
    )

    def below(rel: str, folder: str) -> bool:
        return folder == "" or rel.casefold().startswith(folder.casefold() + "/")

    def covered(rel: str) -> bool:
        return rel.casefold() in listed_files or any(below(rel, d) for d in listed_dirs if d)

    listed_folders = {key.rpartition("/")[0] for key in listed_files}
    siblings = [
        rel
        for rel in index.arxml
        if rel.rpartition("/")[0].casefold() in listed_folders and not covered(rel)
    ]
    workspace_folder = entry.folder
    in_tree = [
        rel for rel in index.arxml if below(rel, workspace_folder) and not covered(rel)
    ]
    by_subfolder: Counter[str] = Counter()
    for rel in in_tree:
        inner = rel[len(workspace_folder) + 1 :] if workspace_folder else rel
        first = inner.partition("/")[0] if "/" in inner else "."
        by_subfolder[names.token(first) if first != "." else "."] += 1
    return {
        "lists_arxml_files": bool(listed),
        "listed": len(listed),
        "listed_existing": len(listed_files),
        "listed_missing": {
            "count": len(missing),
            "items": [_shorten(names.fs_path(raw)) for raw in missing[:MAX_ITEMS]],
        },
        "listed_folders": [
            {
                "folder": names.rel_path(folder, is_dir=True) or ".",
                "arxml_files_inside": sum(1 for rel in index.arxml if below(rel, folder)),
            }
            for folder in listed_dirs[:MAX_ITEMS]
        ],
        "on_disk_not_listed_in_folders_with_listed_files": {
            "count": len(siblings) if listed else 0,
            "items": [names.rel_path(rel) for rel in siblings[:MAX_ITEMS]] if listed else [],
        },
        "on_disk_not_listed_below_this_files_folder": {
            "count": len(in_tree) if listed else 0,
            "by_subfolder": top(by_subfolder, 30) if listed else {},
        },
        "listed_with_different_case": {
            "count": len(mismatches),
            "items": mismatches[:MAX_ITEMS],
        },
    }


def analyze_workspace_file(
    entry: FileEntry, kind: str, index: DiskIndex, ctx: Context
) -> WorkspaceFileResult:
    """Summarise one .dcf or .dpa file. Bad XML is reported, never raised."""
    names = ctx.names
    sink = _StructureSink()
    style = StyleCollector()
    problem: XmlProblem | None
    try:
        problem = feed_xml(read_chunks(entry.path), sink, style)
    except OSError as error:
        problem = XmlProblem("unreadable", type(error).__name__)
    values = list(sink.paths)
    section: dict[str, Any] = {
        "file": names.rel_path(entry.rel),
        "type": kind,
        "bytes": entry.size,
        "xml_ok": problem is None,
        "problem": None,
        "elements": sink.elements,
        "comments": sink.comments,
        "style": style.result().as_dict(),
        "structure": [child.as_dict(names) for child in sink.document.children.values()],
    }
    # the style block of a workspace file can hold names in its root start tag
    style_block = section["style"]
    if names.anonymized and isinstance(style_block, dict):
        raw_tag = style_block.get("root_start_tag")
        if isinstance(raw_tag, str):
            style_block["root_start_tag"] = _safe_root(names, raw_tag)
        location = style_block.get("schema_location")
        if isinstance(location, str):
            style_block["schema_location"] = names.value(location)
        namespace = style_block.get("namespace")
        if isinstance(namespace, str):
            style_block["namespace"] = names.value(namespace)
    if problem is not None:
        section["problem"] = {
            "kind": problem.kind,
            "message": problem.message,
            "line": problem.line,
            "column": problem.column,
        }
        ctx.warning(
            "DISC-WS-MALFORMED",
            f"The {kind} file is not well-formed XML ({problem.describe()}). "
            "The report holds the part that could be read and a text summary.",
            file=entry.rel,
        )
        if problem.kind != "unreadable":
            try:
                raw = _read_head(entry.path, _FALLBACK_BYTES)
            except OSError:
                raw = b""
            codec = style.encoding.codec
            try:
                text = raw.decode(codec, errors="replace")
            except LookupError:
                text = raw.decode("latin-1")
            fallback, scanned = text_fallback(text, names)
            section["text_fallback"] = fallback
            known = {value for _, value in values}
            values.extend(pair for pair in scanned if pair[1] not in known)

    base = normalize_path(os.path.dirname(entry.path))
    resolved = [resolve_value(location, raw, base, index) for location, raw in values]
    relative_values = [(i.location, i.raw) for i in resolved if not i.absolute]
    section["paths"] = _paths_section(resolved, relative_values, index, names)
    section["arxml"] = _arxml_section(entry, resolved, index, names)
    missing = section["arxml"]["listed_missing"]["count"]
    if missing:
        ctx.warning(
            "DISC-WS-MISSING",
            f"{missing} ARXML file(s) named in this {kind} file do not exist on disk.",
            file=entry.rel,
        )
    return WorkspaceFileResult(section=section, resolved=resolved)


def _safe_root(names: Names, raw: str) -> str:
    def replace(match: re.Match[str]) -> str:
        value = match.group(2) if match.group(2) is not None else match.group(3)
        quote = '"' if match.group(2) is not None else "'"
        return f"{match.group(1)}={quote}{names.value(value)}{quote}"

    return re.sub(r"""([\w:.\-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""", replace, raw)


# --------------------------------------------------------------------------- step


@dataclass(slots=True)
class WorkspaceResult:
    section: dict[str, Any]
    dpa_paths: list[ResolvedPath] = field(default_factory=list)


def _ordered(entries: Sequence[FileEntry], preferred: FileEntry | None) -> list[FileEntry]:
    ordered = sorted(entries, key=lambda entry: (entry.rel.count("/"), entry.rel))
    if preferred is not None:
        ordered = [preferred] + [e for e in ordered if e.path != preferred.path]
    return ordered


def build_workspace(
    walk: WalkResult, repo_root: str, ctx: Context, explicit_dpa: FileEntry | None = None
) -> WorkspaceResult:
    """Summarise every .dcf and .dpa file (at most ten of each, the nearest to the root)."""
    index = DiskIndex(repo_root, walk)
    result = WorkspaceResult(section={})
    for kind in ("dcf", "dpa"):
        found = [entry for entry in walk.files if entry.name.lower().endswith("." + kind)]
        ordered = _ordered(found, explicit_dpa if kind == "dpa" else None)
        if not ordered:
            ctx.warning(
                f"DISC-NO-{kind.upper()}",
                f"No .{kind} file was found below the repo root. "
                + (
                    "The workspace index could not be studied."
                    if kind == "dcf"
                    else "Pass --dpa if the project file lies elsewhere."
                ),
            )
        elif len(ordered) > 1:
            ctx.info(
                f"DISC-MANY-{kind.upper()}",
                f"{len(ordered)} .{kind} files were found; each one is summarised separately "
                f"(at most {MAX_FILES_PER_TYPE}).",
            )
        files: list[dict[str, Any]] = []
        for entry in ordered[:MAX_FILES_PER_TYPE]:
            analysed = analyze_workspace_file(entry, kind, index, ctx)
            files.append(analysed.section)
            if kind == "dpa":
                result.dpa_paths.extend(analysed.resolved)
        result.section[kind] = {
            "found": len(ordered),
            "not_analysed": [ctx.names.rel_path(e.rel) for e in ordered[MAX_FILES_PER_TYPE:]],
            "files": files,
        }
    return result
