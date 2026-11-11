"""Report sections computed from the scanned ARXML facts (steps 1, 2, 4, 5, 6, 7, 8).

Every function is pure: facts in, a JSON-ready dict out. Names leave only through
``ctx.names`` so that ``--anonymize`` holds by construction.
"""

from __future__ import annotations

import csv
import fnmatch
import io
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from porttool.discovery.arxml_scan import (
    COMPONENT_KINDS,
    COMPOSITION_KIND,
    NESTED_TAGS,
    ArxmlFacts,
    EcucModuleFacts,
    category_of,
    find_tool_keywords,
)
from porttool.discovery.context import Context, top
from porttool.discovery.fsio import FileEntry, WalkResult, iso_utc
from porttool.discovery.names import (
    STANDARD_DEFINITION_ROOTS,
    STANDARD_URL_PREFIXES,
    Names,
    split_extension,
)

LARGEST_FILES = 20
MAX_FOLDERS = 300
MAX_LIST = 200
MAX_SAMPLES = 3

#: Folder names (one path segment, lower case, glob) that mark generated or service
#: components. PENDING-DISCOVERY: the real repo may use other names; the report lists
#: every folder so the list can be corrected.
LOCKED_FOLDER_PATTERNS: tuple[str, ...] = ("servicecomponent*", "gendata*", "generated*")

LOCK_FILE_PATTERNS: tuple[str, ...] = ("*.lock", "*.lck", "~*", "*.tmp", "*.bak")

_SCHEMA_FILE_RE = re.compile(r"^AUTOSAR_[0-9A-Za-z_.\-]+\.xsd$")
_SCHEMA_VERSION_RE = re.compile(r"AUTOSAR_([0-9A-Za-z\-]+)\.xsd")
_ATTRIBUTE_VALUE_RE = re.compile(r"""([\w:.\-]+)(\s*=\s*)(?:"([^"]*)"|'([^']*)')""", re.DOTALL)


def safe_schema_location(names: Names, value: str | None) -> str | None:
    """Keep standard namespaces and ``AUTOSAR_*.xsd`` readable; map everything else."""
    if value is None or not names.anonymized:
        return value
    parts: list[str] = []
    for part in value.split():
        folder, separator, base = part.replace("\\", "/").rpartition("/")
        if part.startswith(STANDARD_URL_PREFIXES):
            parts.append(part)
        elif _SCHEMA_FILE_RE.match(base):
            parts.append((names.fs_path(folder) + separator if folder else "") + base)
        else:
            parts.append(names.value(part))
    return " ".join(parts)


def safe_start_tag(names: Names, raw: str | None) -> str | None:
    """The root start tag as written; attribute values are mapped when anonymizing."""
    if raw is None or not names.anonymized:
        return raw

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        double = match.group(3)
        value = double if double is not None else match.group(4)
        quote = '"' if double is not None else "'"
        if name.endswith("schemaLocation"):
            shown = safe_schema_location(names, value) or ""
        else:
            shown = names.value(value)
        return f"{name}{match.group(2)}{quote}{shown}{quote}"

    return _ATTRIBUTE_VALUE_RE.sub(replace, raw)


def schema_version_hint(namespace: str | None, schema_location: str | None) -> str:
    """A short label such as ``4-2-2`` or ``00046`` taken from the schema file name."""
    match = _SCHEMA_VERSION_RE.search(schema_location or "")
    if match:
        return match.group(1)
    if namespace:
        return namespace.rstrip("/").rpartition("/")[2]
    return "unknown"


def _folder_label(names: Names, folder: str) -> str:
    return names.rel_path(folder, is_dir=True) if folder else "."


def file_type(name: str) -> str | None:
    """Classify a file name as ``arxml``, ``dcf`` or ``dpa`` (None for other files)."""
    lower = name.lower()
    if lower.endswith(".arxml"):
        return "arxml"
    if lower.endswith(".dcf"):
        return "dcf"
    if lower.endswith(".dpa"):
        return "dpa"
    return None


# --------------------------------------------------------------------------- 1 inventory


def build_inventory(
    walk: WalkResult, facts: Sequence[ArxmlFacts], ctx: Context, exclude: Sequence[str]
) -> dict[str, Any]:
    names = ctx.names
    by_type: dict[str, dict[str, int]] = {
        key: {"count": 0, "bytes": 0} for key in ("arxml", "ecuc.arxml", "dcf", "dpa")
    }
    interesting: list[FileEntry] = []
    for entry in walk.files:
        kind = file_type(entry.name)
        if kind is None:
            continue
        interesting.append(entry)
        by_type[kind]["count"] += 1
        by_type[kind]["bytes"] += entry.size
        if entry.name.lower().endswith(".ecuc.arxml"):
            by_type["ecuc.arxml"]["count"] += 1
            by_type["ecuc.arxml"]["bytes"] += entry.size
    largest = sorted(interesting, key=lambda item: (-item.size, item.rel))[:LARGEST_FILES]

    schemas: Counter[tuple[str, str, str]] = Counter()
    kinds_objects: Counter[str] = Counter()
    kinds_files: Counter[str] = Counter()
    tags_count: Counter[str] = Counter()
    tags_files: Counter[str] = Counter()
    problems: list[dict[str, Any]] = []
    per_file: list[dict[str, Any]] = []
    for item in facts:
        style = item.style
        namespace = style.namespace if style else None
        location = style.schema_location if style else None
        if item.root_tag:
            schemas[
                (
                    namespace or "",
                    safe_schema_location(names, location) or "",
                    schema_version_hint(namespace, location),
                )
            ] += 1
        for kind, count in item.kind_counts.items():
            kinds_objects[kind] += count
            kinds_files[kind] += 1
        for tag, count in item.tag_counts.items():
            tags_count[tag] += count
            tags_files[tag] += 1
        problem = item.problem
        if problem is not None:
            problems.append(
                {
                    "file": names.rel_path(item.rel),
                    "bytes": item.size,
                    "problem": problem.kind,
                    "message": problem.message,
                    "line": problem.line,
                    "column": problem.column,
                }
            )
        per_file.append(
            {
                "file": names.rel_path(item.rel),
                "bytes": item.size,
                "ok": item.ok,
                "problem": problem.kind if problem else None,
                "root": item.root_tag,
                "namespace": namespace,
                "schema_location": safe_schema_location(names, location),
                "elements": item.elements,
                "top_level": top(item.kind_counts),
                "nested": {
                    tag: item.tag_counts[tag] for tag in NESTED_TAGS if tag in item.tag_counts
                },
            }
        )

    categories: dict[str, dict[str, int]] = defaultdict(dict)
    for kind, count in sorted(kinds_objects.items()):
        categories[category_of(kind)][kind] = count
    return {
        "files_in_repo": len(walk.files),
        "folders_in_repo": len(walk.dirs),
        "exclude_globs": list(exclude),
        "skipped_by_exclude": len(walk.skipped),
        "by_type": by_type,
        "largest_files": [
            {"file": names.rel_path(entry.rel), "bytes": entry.size} for entry in largest
        ],
        "schema_versions": [
            {"namespace": key[0], "schema_location": key[1], "version_hint": key[2], "files": n}
            for key, n in sorted(schemas.items(), key=lambda pair: (-pair[1], pair[0]))
        ],
        "top_level_kinds": {
            kind: {
                "objects": kinds_objects[kind],
                "files": kinds_files[kind],
                "category": category_of(kind),
            }
            for kind in top(kinds_objects)
        },
        "categories": {key: dict(value) for key, value in sorted(categories.items())},
        "internal_behaviors": tags_count.get("SWC-INTERNAL-BEHAVIOR", 0),
        "tag_histogram": {
            tag: {"count": tags_count[tag], "files": tags_files[tag]} for tag in top(tags_count)
        },
        "problem_files": problems,
        "arxml_files": per_file,
    }


# --------------------------------------------------------------------------- 2 storage map


def naming_pattern(stem: str, short_name: str) -> tuple[str, str, str]:
    """Relate a file stem to the short name it stores: (pattern, prefix, suffix)."""
    if not short_name:
        return "unrelated", "", ""
    if stem == short_name:
        return "{name}", "", ""
    if stem.lower() == short_name.lower():
        return "{name} (other case)", "", ""
    index = stem.find(short_name)
    label = "{name}"
    if index < 0:
        index = stem.lower().find(short_name.lower())
        label = "{name} (other case)"
    if index < 0:
        return "unrelated", "", ""
    prefix, suffix = stem[:index], stem[index + len(short_name) :]
    pattern = ("{prefix}" if prefix else "") + label + ("{suffix}" if suffix else "")
    return pattern, prefix, suffix


@dataclass(slots=True)
class _KindStore:
    objects: int = 0
    files: int = 0
    single: int = 0
    several: int = 0


def build_storage_map(facts: Sequence[ArxmlFacts], ctx: Context) -> dict[str, Any]:
    names = ctx.names
    stores: dict[str, _KindStore] = defaultdict(_KindStore)
    folder_files: dict[str, Counter[str]] = defaultdict(Counter)
    folder_objects: dict[str, Counter[str]] = defaultdict(Counter)
    packages: dict[str, Counter[str]] = defaultdict(Counter)
    sections: dict[str, Counter[str]] = defaultdict(Counter)
    naming: dict[str, Counter[str]] = defaultdict(Counter)
    naming_samples: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    files_per_kind: dict[str, list[tuple[int, str]]] = defaultdict(list)
    signatures: Counter[tuple[str, ...]] = Counter()
    signature_samples: dict[tuple[str, ...], list[str]] = defaultdict(list)
    locations: dict[tuple[str, str], list[str]] = defaultdict(list)
    layout: Counter[str] = Counter()

    for item in facts:
        if item.objects_total == 0:
            layout["files_without_top_level_object"] += 1
        elif item.objects_total == 1:
            layout["files_with_one_top_level_object"] += 1
        else:
            layout["files_with_several_top_level_objects"] += 1
        if item.kind_counts:
            signature = tuple(sorted(item.kind_counts))
            signatures[signature] += 1
            if len(signature_samples[signature]) < MAX_SAMPLES:
                signature_samples[signature].append(names.rel_path(item.rel))
        stem = split_extension(item.rel.rpartition("/")[2])[0]
        for kind, count in item.kind_counts.items():
            store = stores[kind]
            store.objects += count
            store.files += 1
            if count == 1:
                store.single += 1
            else:
                store.several += 1
            folder_files[kind][item.folder] += 1
            folder_objects[kind][item.folder] += count
            files_per_kind[kind].append((count, item.rel))
        for (kind, package), count in item.packages.items():
            packages[kind][package] += count
        for (kind, section), count in item.sections.items():
            sections[kind][section] += count
        for obj in item.objects:
            locations[(obj.kind, obj.path)].append(item.rel)
            if item.kind_counts[obj.kind] != 1:
                continue
            pattern, prefix, suffix = naming_pattern(stem, obj.name)
            shown = pattern.replace("{prefix}", names.token(prefix)).replace(
                "{suffix}", names.token(suffix)
            )
            naming[obj.kind][shown] += 1
            samples = naming_samples[(obj.kind, shown)]
            if len(samples) < MAX_SAMPLES:
                samples.append(
                    {
                        "file": names.file_name(item.rel.rpartition("/")[2]),
                        "short_name": names.name(obj.name),
                    }
                )

    kinds: dict[str, Any] = {}
    for kind in sorted(stores, key=lambda key: (-stores[key].objects, key)):
        store = stores[kind]
        if store.several == 0:
            kind_layout = "one-object-per-file"
        elif store.single == 0:
            kind_layout = "many-objects-per-file"
        else:
            kind_layout = "mixed"
        folders = sorted(folder_objects[kind].items(), key=lambda pair: (-pair[1], pair[0]))
        biggest = sorted(files_per_kind[kind], key=lambda pair: (-pair[0], pair[1]))[:5]
        kinds[kind] = {
            "category": category_of(kind),
            "objects": store.objects,
            "files": store.files,
            "files_with_exactly_one": store.single,
            "files_with_several": store.several,
            "layout": kind_layout,
            "folders": [
                {
                    "folder": _folder_label(names, folder),
                    "files": folder_files[kind][folder],
                    "objects": count,
                }
                for folder, count in folders[:30]
            ],
            "folders_total": len(folders),
            "packages": [
                {"package": names.ar_path(package), "objects": count}
                for package, count in sorted(
                    packages[kind].items(), key=lambda pair: (-pair[1], pair[0])
                )[:30]
            ],
            "packages_total": len(packages[kind]),
            "file_naming": [
                {"pattern": pattern, "files": count, "samples": naming_samples[(kind, pattern)]}
                for pattern, count in sorted(
                    naming[kind].items(), key=lambda pair: (-pair[1], pair[0])
                )[:10]
            ],
            "child_elements": top(sections[kind]),
            "largest_files": [
                {"file": names.rel_path(rel), "objects": count} for count, rel in biggest
            ],
        }

    split = {key: files for key, files in locations.items() if len(set(files)) > 1}
    return {
        "file_layout": dict(layout),
        "kinds": kinds,
        "file_signatures": [
            {"kinds": list(signature), "files": count, "samples": signature_samples[signature]}
            for signature, count in sorted(signatures.items(), key=lambda p: (-p[1], p[0]))[:40]
        ],
        "split_objects": {
            "count": len(split),
            "samples": [
                {
                    "kind": kind,
                    "path": names.ar_path(path),
                    "files": [names.rel_path(rel) for rel in sorted(set(files))],
                }
                for (kind, path), files in sorted(split.items())[:20]
            ],
        },
    }


# --------------------------------------------------------------------------- 4 XML style


class _StyleBucket:
    """Style statistics of the ARXML files of one folder."""

    FIELDS = (
        "declared_encoding",
        "bom",
        "line_endings",
        "indent",
        "declaration",
        "root_start_tag",
        "root_prefix",
        "trailing_newline",
        "attribute_quote",
    )

    def __init__(self) -> None:
        self.files = 0
        self.counters: dict[str, Counter[str]] = {name: Counter() for name in self.FIELDS}
        self.uuid_formats: Counter[str] = Counter()
        self.uuid_versions: Counter[str] = Counter()
        self.admin_parents: Counter[str] = Counter()
        self.sdg_gids: Counter[str] = Counter()
        self.sd_gids: Counter[str] = Counter()
        self.files_with_admin_data = 0
        self.files_with_head_comment = 0
        self.self_closing = 0
        self.open_close_empty = 0

    def add(self, item: ArxmlFacts, names: Names) -> None:
        style = item.style
        if style is None:
            return
        self.files += 1
        for name in self.FIELDS:
            value = getattr(style, name)
            if name == "root_start_tag":
                value = safe_start_tag(names, value)
            if name == "root_prefix" and value == "":
                value = "(no prefix)"
            self.counters[name]["(none)" if value is None else str(value)] += 1
        self.uuid_formats.update(item.uuid_formats)
        self.uuid_versions.update(item.uuid_versions)
        self.admin_parents.update(item.admin_data_parents)
        self.sdg_gids.update(item.sdg_gids)
        self.sd_gids.update(item.sd_gids)
        self.files_with_admin_data += bool(item.admin_data_parents)
        self.files_with_head_comment += bool(style.head_comments)
        self.self_closing += item.self_closing
        self.open_close_empty += item.open_close_empty

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"files": self.files}
        for name in self.FIELDS:
            out[name] = top(self.counters[name], 5)
        out["uuid_format"] = top(self.uuid_formats, 5)
        out["uuid_version_digit"] = top(self.uuid_versions, 5)
        out["empty_elements"] = {
            "self_closing": self.self_closing,
            "open_close": self.open_close_empty,
        }
        out["files_with_head_comment"] = self.files_with_head_comment
        out["admin_data"] = {
            "files_with_admin_data": self.files_with_admin_data,
            "parent_elements": top(self.admin_parents, 20),
            "sdg_gids": top(self.sdg_gids, 40),
            "sd_gids": top(self.sd_gids, 40),
        }
        return out


def build_xml_style(facts: Sequence[ArxmlFacts], ctx: Context) -> dict[str, Any]:
    overall = _StyleBucket()
    folders: dict[str, _StyleBucket] = defaultdict(_StyleBucket)
    for item in facts:
        if item.style is None or item.size == 0:
            continue
        overall.add(item, ctx.names)
        folders[item.folder].add(item, ctx.names)
    ordered = sorted(folders.items(), key=lambda pair: (-pair[1].files, pair[0]))
    return {
        "overall": overall.as_dict(),
        "folders_total": len(ordered),
        "by_folder": [
            {"folder": _folder_label(ctx.names, folder), **bucket.as_dict()}
            for folder, bucket in ordered[:MAX_FOLDERS]
        ],
    }


# --------------------------------------------------------------------------- 5 ECUC


def _module_json(item: ArxmlFacts, module: EcucModuleFacts, names: Names) -> dict[str, Any]:
    standard = module.definition.startswith(STANDARD_DEFINITION_ROOTS)

    def suffix_out(suffix: str) -> str:
        if not names.anonymized or (standard and not suffix.startswith("/")):
            return suffix
        return names.definition_ref(suffix) if suffix.startswith("/") else names.ar_path(suffix)

    entries = sorted(module.entries.items(), key=lambda pair: (pair[0][2], pair[0][0]))
    references = sorted(module.value_ref_dests.items())
    return {
        "file": names.rel_path(item.rel),
        "module": module.module if standard or not names.anonymized else names.token(module.module),
        "definition": names.definition_ref(module.definition),
        "containers": module.containers,
        "definitions": [
            {
                "element": tag,
                "definition_kind": dest,
                "definition_suffix": suffix_out(suffix),
                "count": count,
            }
            for (tag, dest, suffix), count in entries
        ],
        "reference_targets": [
            {"definition_suffix": suffix_out(suffix), "value_ref_dest": dest, "count": count}
            for (suffix, dest), count in references
        ],
        "definitions_outside_module": module.foreign_definitions,
    }


def build_ecuc(facts: Sequence[ArxmlFacts], ctx: Context) -> dict[str, Any]:
    names = ctx.names
    modules: list[dict[str, Any]] = []
    by_name: Counter[str] = Counter()
    rte: list[dict[str, Any]] = []
    os_modules: list[dict[str, Any]] = []
    mapped = unmapped = tasks = 0
    for item in facts:
        for module in item.ecuc_modules:
            standard = module.definition.startswith(STANDARD_DEFINITION_ROOTS)
            shown_name = (
                module.module if standard or not names.anonymized else names.token(module.module)
            )
            by_name[shown_name or "(no definition)"] += 1
            if len(modules) < 500:
                modules.append(
                    {
                        "file": names.rel_path(item.rel),
                        "module": shown_name,
                        "definition": names.definition_ref(module.definition),
                        "containers": module.containers,
                    }
                )
            if not module.detailed:
                continue
            detail = _module_json(item, module, names)
            if module.module.lower() == "rte":
                detail["event_to_task_mapping"] = {
                    "mapping_containers": module.mapping_containers,
                    "with_task_reference": module.mappings_with_task,
                    "without_task_reference": module.mapping_containers - module.mappings_with_task,
                    "with_event_reference": module.mappings_with_event,
                    "distinct_tasks_referenced": len(module.referenced_tasks),
                    "position_in_task_parameters": sum(
                        count
                        for (_, _, suffix), count in module.entries.items()
                        if suffix.endswith("RtePositionInTask")
                    ),
                }
                mapped += module.mappings_with_task
                unmapped += module.mapping_containers - module.mappings_with_task
                rte.append(detail)
            else:
                task_count = sum(
                    count
                    for (tag, _, suffix), count in module.entries.items()
                    if tag == "ECUC-CONTAINER-VALUE" and suffix == "OsTask"
                )
                detail["tasks"] = task_count
                tasks += task_count
                os_modules.append(detail)
    if not rte:
        ctx.info("DISC-ECUC-NO-RTE", "No Rte module configuration was found in any ARXML file.")
    if not os_modules:
        ctx.info("DISC-ECUC-NO-OS", "No Os module configuration was found in any ARXML file.")
    return {
        "module_configurations": sum(by_name.values()),
        "modules_by_name": top(by_name),
        "module_files": modules,
        "rte": rte,
        "os": os_modules,
        "summary": {
            "rte_module_configurations": len(rte),
            "os_module_configurations": len(os_modules),
            "events_mapped_to_a_task": mapped,
            "event_mappings_without_task": unmapped,
            "os_tasks": tasks,
            "note": (
                "Counts use the AUTOSAR standard definition names RteEventToTaskMapping, "
                "RteMappedToTaskRef, RteEventRef, RtePositionInTask and OsTask. If they are "
                "zero, read the definition tables: they list what the file really holds."
            ),
        },
    }


# --------------------------------------------------------------------------- 6 composition


def build_composition(facts: Sequence[ArxmlFacts], ctx: Context) -> dict[str, Any]:
    names = ctx.names
    file_of: dict[str, str] = {}
    children: dict[str, list[str]] = {}
    items: list[dict[str, Any]] = []
    connector_files: list[dict[str, Any]] = []
    assembly_total = delegation_total = pass_total = 0
    for item in facts:
        file_assembly = file_delegation = 0
        for composition in item.compositions:
            file_of[composition.path] = item.rel
            children[composition.path] = [ref for _, ref, _ in composition.prototypes if ref]
            file_assembly += composition.assembly
            file_delegation += composition.delegation
            pass_total += composition.pass_through
        if file_assembly or file_delegation:
            connector_files.append(
                {
                    "file": names.rel_path(item.rel),
                    "assembly": file_assembly,
                    "delegation": file_delegation,
                }
            )
        assembly_total += file_assembly
        delegation_total += file_delegation

    used_as_prototype: set[str] = set()
    nested_in: dict[str, int] = {}
    for path, refs in children.items():
        inner = [ref for ref in refs if ref in children]
        nested_in[path] = len(inner)
        used_as_prototype.update(inner)

    def depth_of(path: str, seen: frozenset[str]) -> int:
        deepest = 1
        for ref in children.get(path, ()):
            if ref in children and ref not in seen:
                deepest = max(deepest, 1 + depth_of(ref, seen | {ref}))
        return deepest

    for item in facts:
        for composition in item.compositions:
            if len(items) >= MAX_LIST:
                break
            dest_composition = sum(
                1 for _, _, dest in composition.prototypes if dest == COMPOSITION_KIND
            )
            items.append(
                {
                    "path": names.ar_path(composition.path),
                    "file": names.rel_path(item.rel),
                    "prototypes": len(composition.prototypes),
                    "composition_prototypes": max(nested_in[composition.path], dest_composition),
                    "assembly_connectors": composition.assembly,
                    "delegation_connectors": composition.delegation,
                    "pass_through_connectors": composition.pass_through,
                }
            )

    references: list[dict[str, str]] = []
    referenced: dict[str, str] = {}
    for item in facts:
        for ref in item.root_composition_refs:
            referenced.setdefault(ref, item.rel)
            if len(references) < MAX_LIST:
                references.append({"ref": names.ar_path(ref), "file": names.rel_path(item.rel)})
    candidates: list[dict[str, str]] = []
    for path in sorted(children):
        reasons: list[str] = []
        if path in referenced:
            reasons.append("referenced by SOFTWARE-COMPOSITION-TREF (root of a SYSTEM)")
        if path not in used_as_prototype:
            reasons.append("not used as a prototype in any other composition")
        if reasons:
            candidates.append(
                {
                    "path": names.ar_path(path),
                    "file": names.rel_path(file_of[path]),
                    "reason": "; ".join(reasons),
                }
            )
    return {
        "compositions": len(children),
        "nested": any(count for count in nested_in.values()),
        "max_nesting_depth": max((depth_of(p, frozenset({p})) for p in children), default=0),
        "assembly_connectors": assembly_total,
        "delegation_connectors": delegation_total,
        "pass_through_connectors": pass_total,
        "connector_files": connector_files[:MAX_LIST],
        "items": items,
        "root_candidates": candidates[:MAX_LIST],
        "root_composition_references": references,
    }


# --------------------------------------------------------------------------- 7 owners


@dataclass(frozen=True, slots=True)
class OwnerRow:
    swc: str
    path: str
    file: str
    kind: str
    owner: str  # "model", "davinci" or "locked"
    authoring_tool: str
    reason: str


@dataclass(frozen=True, slots=True)
class OwnersResult:
    section: dict[str, Any]
    owners_csv: str  # written into the report folder (anonymized when asked)
    locked_txt: str
    owners_csv_private: str  # real names, for the owner's private folder
    locked_txt_private: str


def locked_folder_reason(folder: str) -> str | None:
    for segment in folder.lower().split("/"):
        for pattern in LOCKED_FOLDER_PATTERNS:
            if fnmatch.fnmatchcase(segment, pattern):
                return f"folder name matches {pattern}"
    return None


def _authoring_tool(keywords: Iterable[str]) -> str:
    found = set(keywords)
    if found & {"TargetLink", "dSPACE"}:
        return "TargetLink"
    return "Simulink" if found else ""


def _owners_csv(rows: Sequence[OwnerRow], names: Names) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["swc", "team", "arxml_owner", "authoring_tool", "export_folder"])
    seen: set[str] = set()
    for row in rows:
        if row.owner == "locked" or row.swc in seen:
            continue
        seen.add(row.swc)
        folder = row.file.rpartition("/")[0]
        export = names.rel_path(folder, is_dir=True) if row.owner == "model" and folder else ""
        writer.writerow([names.name(row.swc), "", row.owner, row.authoring_tool, export])
    return buffer.getvalue()


def _locked_txt(rows: Sequence[OwnerRow], names: Names) -> str:
    listed = sorted({names.name(row.swc) for row in rows if row.owner == "locked"})
    return "".join(name + "\n" for name in listed)


def build_owners(facts: Sequence[ArxmlFacts], ctx: Context) -> OwnersResult:
    names = ctx.names
    mentions_by_file: dict[str, Counter[tuple[str, str]]] = {}
    implementation_hint: dict[str, set[str]] = defaultdict(set)
    for item in facts:
        mentions: Counter[tuple[str, str]] = Counter(item.mentions)
        for gid in list(item.sdg_gids) + list(item.sd_gids):
            for keyword in find_tool_keywords(gid):
                mentions[(keyword, "GID")] += 1
        for keyword in find_tool_keywords(item.rel):
            mentions[(keyword, "file path")] += 1
        if mentions:
            mentions_by_file[item.rel] = mentions
            for ref in item.behavior_refs:
                swc_path = ref.rpartition("/")[0]
                implementation_hint[swc_path].update(keyword for keyword, _ in mentions)

    rows: list[OwnerRow] = []
    evidence: dict[str, list[dict[str, Any]]] = {}
    for item in facts:
        file_mentions = mentions_by_file.get(item.rel, Counter())
        for obj in item.objects:
            if obj.kind not in COMPONENT_KINDS and obj.kind != COMPOSITION_KIND:
                continue
            keywords = {keyword for keyword, _ in file_mentions}
            keywords |= implementation_hint.get(obj.path, set())
            folder_reason = locked_folder_reason(item.folder)
            if obj.kind == "SERVICE-SW-COMPONENT-TYPE":
                owner, reason = "locked", "service component type"
            elif folder_reason is not None:
                owner, reason = "locked", folder_reason
            elif keywords:
                owner, reason = "model", "file mentions " + ", ".join(sorted(keywords))
            else:
                owner, reason = "davinci", ""
            rows.append(
                OwnerRow(
                    swc=obj.name,
                    path=obj.path,
                    file=item.rel,
                    kind=obj.kind,
                    owner=owner,
                    authoring_tool=_authoring_tool(keywords) if owner == "model" else "",
                    reason=reason,
                )
            )
            if owner == "model":
                listed = [
                    {"keyword": keyword, "where": where, "count": count}
                    for (keyword, where), count in sorted(file_mentions.items())
                ]
                for keyword in sorted(implementation_hint.get(obj.path, set())):
                    listed.append({"keyword": keyword, "where": "implementation file", "count": 1})
                evidence[obj.path] = listed
    rows.sort(key=lambda row: (row.swc, row.path))

    def shown(row: OwnerRow) -> dict[str, Any]:
        out: dict[str, Any] = {
            "swc": names.name(row.swc),
            "path": names.ar_path(row.path),
            "file": names.rel_path(row.file),
            "kind": row.kind,
        }
        if row.owner == "model":
            out["authoring_tool"] = row.authoring_tool
            out["evidence"] = evidence.get(row.path, [])
        if row.owner == "locked":
            out["reason"] = row.reason
        return out

    duplicates = [name for name, count in Counter(row.swc for row in rows).items() if count > 1]
    section = {
        "component_types": len(rows),
        "proposed_davinci": sum(1 for row in rows if row.owner == "davinci"),
        "proposed_model": sum(1 for row in rows if row.owner == "model"),
        "locked_candidates_total": sum(1 for row in rows if row.owner == "locked"),
        "model_candidates": [shown(row) for row in rows if row.owner == "model"][:MAX_LIST],
        "locked_candidates": [shown(row) for row in rows if row.owner == "locked"][:MAX_LIST],
        "short_names_used_twice": len(duplicates),
        "files_mentioning_authoring_tools": [
            {
                "file": names.rel_path(rel),
                "mentions": [
                    {"keyword": keyword, "where": where, "count": count}
                    for (keyword, where), count in sorted(mentions.items())
                ],
            }
            for rel, mentions in sorted(mentions_by_file.items())[:MAX_LIST]
        ],
        "locked_folder_patterns": list(LOCKED_FOLDER_PATTERNS),
        "proposed_files": ["owners_proposed.csv", "locked_proposed.txt"],
    }
    plain = Names()
    return OwnersResult(
        section=section,
        owners_csv=_owners_csv(rows, names),
        locked_txt=_locked_txt(rows, names),
        owners_csv_private=_owners_csv(rows, plain),
        locked_txt_private=_locked_txt(rows, plain),
    )


# --------------------------------------------------------------------------- 8 lock signs


def lock_pattern(name: str) -> str | None:
    lower = name.lower()
    for pattern in LOCK_FILE_PATTERNS:
        if fnmatch.fnmatchcase(lower, pattern):
            return pattern
    return None


def build_lock_signs(walk: WalkResult, ctx: Context) -> dict[str, Any]:
    names = ctx.names
    workspace_folders = {entry.folder for entry in walk.files if file_type(entry.name) is not None}
    items: list[dict[str, Any]] = []
    total = in_workspace = 0
    by_pattern: Counter[str] = Counter()
    for entry in walk.files:
        pattern = lock_pattern(entry.name)
        if pattern is None:
            continue
        total += 1
        by_pattern[pattern] += 1
        inside = entry.folder in workspace_folders
        in_workspace += inside
        if len(items) < MAX_LIST:
            items.append(
                {
                    "file": names.rel_path(entry.rel),
                    "pattern": pattern,
                    "bytes": entry.size,
                    "modified_utc": iso_utc(entry.mtime_ns),
                    "in_workspace_folder": inside,
                }
            )
    if in_workspace:
        ctx.warning(
            "DISC-LOCK-FOUND",
            f"{in_workspace} lock or temporary file(s) lie next to workspace files. "
            "A DaVinci tool may be open, or it crashed earlier.",
        )
    items.sort(key=lambda item: (not item["in_workspace_folder"], item["file"]))
    return {
        "patterns": list(LOCK_FILE_PATTERNS),
        "count": total,
        "in_workspace_folders": in_workspace,
        "by_pattern": top(by_pattern),
        "items": items,
    }
