"""Readable outputs: discovery.md, golden_diff_*.md, README.txt and the console summary.

Everything here formats values that are already in the JSON report; nothing reads the
repo, so the Markdown can never hold more than the JSON does.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

PLAIN_STATEMENT: tuple[str, ...] = (
    "Mode: NOT anonymized. This report contains:",
    "- counts, sizes, XML tag names, attribute names and schema versions;",
    "- file and folder names below the repo root, written relative to the repo root "
    "(the absolute repo path, your user name and your computer name are not written);",
    "- AUTOSAR short names and package paths of component types, compositions, port "
    "interfaces, data types and other top-level elements, and the git branch name;",
    "- up to three sample values per element and attribute of the .dcf and .dpa files "
    "(typically file paths, versions and tool settings), as written in those files;",
    "- GID names of ADMIN-DATA/SDG blocks and DEFINITION-REF paths of the ECUC file;",
    "- the paths of the Vector tools that were found and the help text they printed.",
    "It does NOT contain: init values, constants, descriptions, comment texts, ECUC "
    "parameter values, ECUC container names or source code.",
    "Exception: a golden-diff report holds the complete XML text of the elements that "
    "were ADDED between the two commits, including the names and values inside them. "
    "For changed elements it holds only the shape of the old and new value "
    "(letters as a, digits as 9).",
)

ANONYMOUS_STATEMENT: tuple[str, ...] = (
    "Mode: ANONYMIZED (--anonymize). In this report:",
    "- every AUTOSAR short name, package path segment, file name stem, folder name below "
    "the repo root and the git branch name is replaced by a token such as N1a2b3c; the "
    "same name always gives the same token;",
    "- file extensions, XML tag names, attribute names, counts and structure are kept;",
    "- sample values of the .dcf and .dpa files are kept only when they are numbers, "
    "booleans, UUIDs, version numbers, dates or standard AUTOSAR/W3C namespaces; paths "
    "are replaced segment by segment, every other value by a token;",
    "- kept readable on purpose: ECUC definition paths below /MICROSAR/ and /AUTOSAR/, "
    "GID names of SDG blocks, UUID values, AUTOSAR_*.xsd schema names, and the folder "
    "and file names of the Vector tools (Program Files, Vector DaVinci Developer*, "
    "DaVinciConfigurator, Core, Bin, DV*.exe);",
    "- the help text of the Vector tools (cli/*.txt) is kept, with the repo path, your "
    "user name, computer name and home folder replaced by placeholders. READ these files "
    "before you send them;",
    "- a golden-diff report holds the ADDED elements re-written with tokens: numbers, "
    "booleans and AUTOSAR enumeration literals stay, comments are removed.",
    "It does NOT contain: init values, constants, descriptions, comment texts, ECUC "
    "parameter values, ECUC container names or source code.",
)


def mode_statement(anonymized: bool, command: str) -> list[str]:
    """Exactly what a report of the chosen mode includes. Used in README.txt and the .md."""
    lines = list(ANONYMOUS_STATEMENT if anonymized else PLAIN_STATEMENT)
    if command == "run":
        lines = [line for line in lines if "golden-diff report" not in line]
    return lines


# --------------------------------------------------------------------------- helpers


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    text = str(value).replace("\r", " ").replace("\n", " ").replace("|", "\\|")
    return text if text else "-"


def _table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> list[str]:
    body = [list(row) for row in rows]
    if not body:
        return ["(none)", ""]
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(" --- " for _ in headers) + "|"]
    lines += ["| " + " | ".join(_cell(value) for value in row) + " |" for row in body]
    lines.append("")
    return lines


def _counts(counter: Mapping[str, Any] | None, limit: int = 4, code: bool = False) -> str:
    if not counter:
        return "-"
    mark = "`" if code else ""
    parts = [f"{mark}{key}{mark} ({value})" for key, value in list(counter.items())[:limit]]
    extra = len(counter) - limit
    return "; ".join(parts) + (f"; +{extra} more" if extra > 0 else "")


def _megabytes(size: int) -> str:
    return f"{size / 1_000_000:.2f} MB"


def _get(data: Any, *keys: str, default: Any = None) -> Any:
    for key in keys:
        if not isinstance(data, dict) or key not in data:
            return default
        data = data[key]
    return data


def _findings(report: Mapping[str, Any], limit: int = 100) -> list[str]:
    findings = report.get("findings", [])
    lines = [f"## Findings ({len(findings)})", ""]
    lines += _table(
        ("Severity", "Code", "Step", "File", "Message"),
        ((f["severity"], f["code"], f["step"], f["file"], f["message"]) for f in findings[:limit]),
    )
    if len(findings) > limit:
        lines += [f"{len(findings) - limit} more findings are in the JSON file.", ""]
    return lines


def _head(report: Mapping[str, Any], title: str) -> list[str]:
    tool = report.get("tool", {})
    lines = [f"# {title}", ""]
    lines += _get(report, "mode", "includes", default=[])
    lines += [
        "",
        f"Created (UTC): {report.get('created_utc')}  ",
        f"Tool: {tool.get('name')} {tool.get('version')}, report format "
        f"{report.get('format_version')}  ",
        f"Python {_get(report, 'environment', 'python')} on "
        f"{_get(report, 'environment', 'platform')}",
        "",
    ]
    return lines


# --------------------------------------------------------------------------- discovery.md


def _structure_lines(nodes: Sequence[Mapping[str, Any]], depth: int, out: list[str]) -> None:
    for node in nodes:
        parts = [f"{'  ' * depth}- `{node['tag']}` x{node['count']}"]
        for name, leaf in node.get("attributes", {}).items():
            samples = ", ".join(f"`{sample}`" for sample in leaf["samples"])
            parts.append(f"@{name} ({leaf['count']}x, {leaf['distinct']} distinct: {samples})")
        text = node.get("text")
        if text:
            samples = ", ".join(f"`{sample}`" for sample in text["samples"])
            parts.append(f"text ({text['count']}x, {text['distinct']} distinct: {samples})")
        out.append("; ".join(parts))
        if depth < 12:
            _structure_lines(node.get("children", []), depth + 1, out)


def _workspace_lines(section: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    for kind in ("dcf", "dpa"):
        block = section.get(kind, {})
        lines += [f"### .{kind} files: {block.get('found', 0)} found", ""]
        for item in block.get("files", []):
            style = item.get("style", {})
            paths = item.get("paths", {})
            arxml = item.get("arxml", {})
            lines += [
                f"#### `{item['file']}` ({item['bytes']} bytes)",
                "",
                f"- well-formed XML: {_cell(item['xml_ok'])}"
                + (
                    f" ({item['problem']['message']}, line {item['problem']['line']})"
                    if item.get("problem")
                    else ""
                ),
                f"- declaration: `{style.get('declaration')}`; BOM: {style.get('bom')}; line "
                f"endings: {style.get('line_endings')}; indentation: {style.get('indent')}; "
                f"trailing newline: {style.get('trailing_newline')}",
                f"- root start tag: `{_cell(style.get('root_start_tag'))}`",
                f"- values that look like paths: {paths.get('values_that_look_like_paths')} "
                f"(relative {paths.get('relative')}, absolute {paths.get('absolute')}, with "
                f"variables {paths.get('with_variables')}); separators: "
                f"{_counts(paths.get('separators'))}",
                "- relative paths that exist when resolved against the file's own folder: "
                f"{_get(paths, 'relative_resolved_against_the_files_folder', 'existing')}, "
                "against the repo root: "
                f"{_get(paths, 'relative_resolved_against_the_repo_root', 'existing')}",
                f"- ARXML files listed: {arxml.get('listed')}, of these on disk: "
                f"{arxml.get('listed_existing')}, listed but missing: "
                f"{_get(arxml, 'listed_missing', 'count')}",
                "- ARXML files on disk but not listed, in folders that hold listed files: "
                f"{_get(arxml, 'on_disk_not_listed_in_folders_with_listed_files', 'count')}; "
                "anywhere below this file's folder: "
                f"{_get(arxml, 'on_disk_not_listed_below_this_files_folder', 'count')}",
                "",
                "Where the path values are:",
                "",
            ]
            lines += _table(
                ("Location", "Count", "Files", "Folders", "Missing", "Extensions", "Samples"),
                (
                    (
                        f"`{row['location']}`",
                        row["count"],
                        row["existing_files"],
                        row["existing_folders"],
                        row["missing"],
                        _counts(row["extensions"]),
                        ", ".join(f"`{sample}`" for sample in row["samples"]),
                    )
                    for row in paths.get("by_location", [])[:25]
                ),
            )
            for label, key in (
                ("Listed but missing", "listed_missing"),
                ("On disk but not listed", "on_disk_not_listed_in_folders_with_listed_files"),
            ):
                items = _get(arxml, key, "items", default=[])
                if items:
                    lines += [f"{label}: " + ", ".join(f"`{i}`" for i in items[:20]), ""]
            lines += ["Structure (tag, occurrences, attributes, sample values):", ""]
            tree: list[str] = []
            _structure_lines(item.get("structure", []), 0, tree)
            lines += tree[:400] + [""]
            fallback = item.get("text_fallback")
            if fallback:
                lines += [f"Text summary ({fallback['lines']} lines); {fallback['note']}", ""]
                lines += _table(
                    ("Line shape", "Lines"),
                    ((f"`{row['shape']}`", row["lines"]) for row in fallback["line_shapes"]),
                )
    return lines


def _ecuc_lines(section: Mapping[str, Any]) -> list[str]:
    summary = section.get("summary", {})
    lines = [
        f"- module configurations: {section.get('module_configurations')} "
        f"({_counts(section.get('modules_by_name'), 12)})",
        f"- Rte module configurations: {summary.get('rte_module_configurations')}; events "
        f"mapped to a task: {summary.get('events_mapped_to_a_task')}; mapping containers "
        f"without a task: {summary.get('event_mappings_without_task')}",
        f"- Os module configurations: {summary.get('os_module_configurations')}; tasks: "
        f"{summary.get('os_tasks')}",
        f"- {summary.get('note', '')}",
        "",
    ]
    for key in ("rte", "os"):
        for module in section.get(key, []):
            lines += [
                f"### {module['module']} in `{module['file']}`",
                "",
                f"Definition: `{module['definition']}`; containers: {module['containers']}",
                "",
            ]
            mapping = module.get("event_to_task_mapping")
            if mapping:
                lines += [", ".join(f"{k}: {v}" for k, v in mapping.items()), ""]
            lines += _table(
                ("Element", "Definition kind", "Definition path below the module", "Count"),
                (
                    (
                        row["element"],
                        row["definition_kind"],
                        f"`{row['definition_suffix']}`",
                        row["count"],
                    )
                    for row in module.get("definitions", [])[:150]
                ),
            )
            lines += ["Targets of reference values:", ""]
            lines += _table(
                ("Definition path below the module", "DEST of VALUE-REF", "Count"),
                (
                    (f"`{row['definition_suffix']}`", row["value_ref_dest"], row["count"])
                    for row in module.get("reference_targets", [])[:80]
                ),
            )
    return lines


def _tools_lines(section: Mapping[str, Any]) -> list[str]:
    if section.get("skipped"):
        return [str(section.get("note", "")), ""]
    lines = [
        f"Help flags tried, one at a time: {', '.join(section.get('help_flags', []))}; "
        f"timeout per start: {section.get('timeout_s')} s.",
        f"Not found: {', '.join(section.get('not_found', [])) or 'none'}.",
        "",
    ]
    for item in section.get("executables", []):
        lines += [
            f"### {item['tool']}: `{item['path']}`",
            "",
            f"Found via {item['found_via']}; {item['bytes']} bytes; modified "
            f"{item['modified_utc']}; file version {_cell(item['file_version'])}; product "
            f"version {_cell(item['product_version'])}; {_counts(item.get('version_strings'), 6)}",
            "",
        ]
        lines += _table(
            (
                "Flag",
                "Exit code",
                "Timed out",
                "Seconds",
                "stdout bytes",
                "stderr bytes",
                "Looks like help",
                "Output file",
            ),
            (
                (
                    f"`{a['flag']}`",
                    a["exit_code"],
                    a["timed_out"],
                    a["duration_s"],
                    a["stdout_bytes"],
                    a["stderr_bytes"],
                    a["looks_like_help"],
                    a["output_file"],
                )
                for a in item.get("attempts", [])
            ),
        )
    return lines


def render_discovery_md(report: Mapping[str, Any]) -> str:
    """The readable summary of ``discover run``."""
    lines = _head(report, "PortTool discovery report")
    lines += ["## Steps", ""]
    lines += _table(
        ("Step", "OK", "Seconds", "Findings"),
        ((s["title"], s["ok"], s["seconds"], s["findings"]) for s in report.get("steps", [])),
    )
    lines += _findings(report)

    inventory = report.get("inventory", {})
    lines += ["## 1 Inventory", ""]
    lines += _table(
        ("Type", "Files", "Size"),
        (
            (f"*.{kind}", value["count"], _megabytes(value["bytes"]))
            for kind, value in inventory.get("by_type", {}).items()
        ),
    )
    lines += ["AUTOSAR schema versions:", ""]
    lines += _table(
        ("Namespace", "schemaLocation", "Version hint", "Files"),
        (
            (row["namespace"], row["schema_location"], row["version_hint"], row["files"])
            for row in inventory.get("schema_versions", [])
        ),
    )
    lines += ["Top-level element kinds (children of AR-PACKAGE/ELEMENTS):", ""]
    lines += _table(
        ("Kind", "Category", "Objects", "Files"),
        (
            (kind, value["category"], value["objects"], value["files"])
            for kind, value in inventory.get("top_level_kinds", {}).items()
        ),
    )
    lines += [f"Internal behaviors: {inventory.get('internal_behaviors')}", ""]
    lines += ["Largest files:", ""]
    lines += _table(
        ("File", "Size"),
        (
            (f"`{row['file']}`", _megabytes(row["bytes"]))
            for row in inventory.get("largest_files", [])[:10]
        ),
    )
    lines += ["Files that are empty, malformed or unreadable:", ""]
    lines += _table(
        ("File", "Problem", "Message", "Line"),
        (
            (f"`{row['file']}`", row["problem"], row["message"], row["line"])
            for row in inventory.get("problem_files", [])[:50]
        ),
    )

    storage = report.get("storage_map", {})
    lines += ["## 2 Storage map", "", f"File layout: {_counts(storage.get('file_layout'))}", ""]
    for kind, value in list(storage.get("kinds", {}).items())[:40]:
        lines += [
            f"### {kind}",
            "",
            f"- {value['objects']} objects in {value['files']} files; layout: "
            f"**{value['layout']}** ({value['files_with_exactly_one']} files with one, "
            f"{value['files_with_several']} with several)",
            "- folders: "
            + "; ".join(f"`{f['folder']}` ({f['objects']})" for f in value["folders"][:5]),
            "- packages: "
            + "; ".join(f"`{p['package']}` ({p['objects']})" for p in value["packages"][:5]),
            "- file name pattern: "
            + (
                "; ".join(f"`{n['pattern']}` ({n['files']})" for n in value["file_naming"][:4])
                or "-"
            ),
            f"- child elements: {_counts(value['child_elements'], 12)}",
            "",
        ]
    lines += ["Kinds stored together in one file:", ""]
    lines += _table(
        ("Kinds in the file", "Files", "Samples"),
        (
            (" + ".join(row["kinds"]), row["files"], ", ".join(f"`{s}`" for s in row["samples"]))
            for row in storage.get("file_signatures", [])[:15]
        ),
    )
    lines += [
        f"Objects defined in more than one file: {_get(storage, 'split_objects', 'count')}",
        "",
    ]

    lines += ["## 3 Workspace index", ""]
    lines += _workspace_lines(report.get("workspace", {}))

    style = report.get("xml_style", {})
    overall = style.get("overall", {})
    lines += ["## 4 XML style", ""]
    lines += [
        f"All {overall.get('files')} ARXML files together:",
        "",
        f"- declaration: {_counts(overall.get('declaration'), code=True)}",
        f"- declared encoding: {_counts(overall.get('declared_encoding'))}; BOM: "
        f"{_counts(overall.get('bom'))}",
        f"- line endings: {_counts(overall.get('line_endings'))}; trailing newline: "
        f"{_counts(overall.get('trailing_newline'))}",
        f"- indentation: {_counts(overall.get('indent'))}; attribute quotes: "
        f"{_counts(overall.get('attribute_quote'))}",
        f"- root prefix: {_counts(overall.get('root_prefix'))}",
        f"- root start tag: {_counts(overall.get('root_start_tag'), 2, code=True)}",
        f"- UUID format: {_counts(overall.get('uuid_format'))}; UUID version digit: "
        f"{_counts(overall.get('uuid_version_digit'))}",
        f"- empty elements: {_counts(overall.get('empty_elements'))}",
        f"- ADMIN-DATA in {_get(overall, 'admin_data', 'files_with_admin_data')} files, below: "
        f"{_counts(_get(overall, 'admin_data', 'parent_elements'), 8)}",
        f"- SDG GID names: {_counts(_get(overall, 'admin_data', 'sdg_gids'), 15)}",
        f"- SD GID names: {_counts(_get(overall, 'admin_data', 'sd_gids'), 15)}",
        "",
        f"Per folder ({style.get('folders_total')} folders, largest first):",
        "",
    ]
    lines += _table(
        (
            "Folder",
            "Files",
            "Encoding",
            "BOM",
            "Line endings",
            "Indent",
            "Trailing newline",
            "UUID format",
        ),
        (
            (
                f"`{row['folder']}`",
                row["files"],
                _counts(row["declared_encoding"], 2),
                _counts(row["bom"], 2),
                _counts(row["line_endings"], 2),
                _counts(row["indent"], 2),
                _counts(row["trailing_newline"], 2),
                _counts(row["uuid_format"], 2),
            )
            for row in style.get("by_folder", [])[:40]
        ),
    )

    lines += ["## 5 ECUC", ""]
    lines += _ecuc_lines(report.get("ecuc", {}))

    composition = report.get("composition", {})
    lines += [
        "## 6 Composition shape",
        "",
        f"- compositions: {composition.get('compositions')}; nested: "
        f"{_cell(composition.get('nested'))}; deepest nesting: "
        f"{composition.get('max_nesting_depth')}",
        f"- assembly connectors: {composition.get('assembly_connectors')}; delegation "
        f"connectors: {composition.get('delegation_connectors')}",
        "",
    ]
    lines += _table(
        ("Composition", "File", "Prototypes", "Of compositions", "Assembly", "Delegation"),
        (
            (
                f"`{row['path']}`",
                f"`{row['file']}`",
                row["prototypes"],
                row["composition_prototypes"],
                row["assembly_connectors"],
                row["delegation_connectors"],
            )
            for row in composition.get("items", [])[:40]
        ),
    )
    lines += ["Root (ECU) composition candidates:", ""]
    lines += _table(
        ("Composition", "File", "Why"),
        (
            (f"`{row['path']}`", f"`{row['file']}`", row["reason"])
            for row in composition.get("root_candidates", [])[:20]
        ),
    )

    owners = report.get("owners", {})
    lines += [
        "## 7 Model-owned and locked candidates",
        "",
        f"Component types: {owners.get('component_types')}; proposed davinci: "
        f"{owners.get('proposed_davinci')}; proposed model: {owners.get('proposed_model')}; "
        f"locked candidates: {owners.get('locked_candidates_total')}. The proposal is in "
        "owners_proposed.csv and locked_proposed.txt: check it by hand.",
        "",
    ]
    lines += _table(
        ("SWC", "File", "Authoring tool", "Evidence"),
        (
            (
                f"`{row['swc']}`",
                f"`{row['file']}`",
                row.get("authoring_tool"),
                "; ".join(f"{e['keyword']} in {e['where']}" for e in row.get("evidence", [])),
            )
            for row in owners.get("model_candidates", [])[:60]
        ),
    )
    lines += ["Locked candidates:", ""]
    lines += _table(
        ("SWC", "File", "Why"),
        (
            (f"`{row['swc']}`", f"`{row['file']}`", row.get("reason"))
            for row in owners.get("locked_candidates", [])[:60]
        ),
    )

    locks = report.get("lock_signs", {})
    lines += [
        "## 8 Lock and temporary files",
        "",
        f"{locks.get('count')} found, {locks.get('in_workspace_folders')} of them next to "
        "workspace files.",
        "",
    ]
    lines += _table(
        ("File", "Pattern", "Modified (UTC)", "Next to workspace files"),
        (
            (f"`{row['file']}`", row["pattern"], row["modified_utc"], row["in_workspace_folder"])
            for row in locks.get("items", [])[:40]
        ),
    )

    git = report.get("git", {})
    lines += ["## 9 Git state", ""]
    if not git.get("available"):
        lines += ["git could not be started.", ""]
    elif not git.get("is_repo"):
        lines += [f"{git.get('version')}; the repo folder is not a git work tree.", ""]
    else:
        lines += [
            f"- {git.get('version')}; branch: `{_cell(git.get('branch'))}`; HEAD: "
            f"{git.get('head_commit')}",
            f"- clean: {_cell(git.get('clean'))}; changes: {_counts(git.get('changes'))}",
            "- config: " + ", ".join(f"{k}={_cell(v)}" for k, v in git.get("config", {}).items()),
            "- attributes of one sample file per type: "
            + (
                "; ".join(
                    f"{kind}: " + ", ".join(f"{k}={v}" for k, v in values.items())
                    for kind, values in git.get(
                        "attributes_of_one_sample_file_per_type", {}
                    ).items()
                )
                or "-"
            ),
            "",
        ]
        lines += _table(
            ("Generated folder", "Ignored by git", "Tracked files"),
            (
                (f"`{row['folder']}`", row["ignored"], row["tracked_files"])
                for row in git.get("generated_folders", [])
            ),
        )

    lines += ["## 10 Vector command-line tools", ""]
    lines += _tools_lines(report.get("tools", {}))
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- golden diff


def render_golden_md(report: Mapping[str, Any]) -> str:
    detail = report.get("golden_diff", {})
    lines = _head(report, "PortTool golden diff")
    summary = detail.get("summary", {})
    lines += [
        f"Base commit: {detail.get('base_commit')}  ",
        f"Head commit: {detail.get('head_commit')}  ",
        "git config: "
        + ", ".join(f"{k}={_cell(v)}" for k, v in detail.get("git_config", {}).items()),
        "",
        str(detail.get("note", "")),
        "",
        "## Summary",
        "",
        ", ".join(f"{key}: {value}" for key, value in summary.items()),
        "",
    ]
    lines += _findings(report)
    lines += ["## Changed files", ""]
    lines += _table(
        ("File", "Status", "XML", "Added", "Removed", "Changed", "Byte-level changes", "Note"),
        (
            (
                f"`{item['path']}`",
                item["status"],
                item["xml"],
                _get(item, "summary", "elements_added"),
                _get(item, "summary", "elements_removed"),
                _get(item, "summary", "elements_changed"),
                ", ".join(_get(item, "bytes", "changed", default=[])) or "-",
                item.get("skipped")
                or (
                    "only line endings changed"
                    if _get(item, "summary", "only_line_endings_changed")
                    else ""
                )
                or ("only layout changed" if _get(item, "summary", "only_layout_changed") else ""),
            )
            for item in detail.get("files", [])[:500]
        ),
    )
    for item in detail.get("files", []):
        if "elements" not in item:
            continue
        lines += [f"## `{item['path']}` ({item['status']})", ""]
        for side in ("base", "head"):
            style = _get(item, "bytes", side)
            if style:
                lines.append(
                    f"- {side}: {item.get(side + '_bytes')} bytes; line endings "
                    f"{style['line_endings']} (CRLF {style['crlf']}, LF {style['lf']}); "
                    f"indentation {style['indent']}; BOM {style['bom']}; trailing newline "
                    f"{style['trailing_newline']}; declaration `{style['declaration']}`"
                )
        lines += [
            "- byte-level facts that changed: "
            + (", ".join(_get(item, "bytes", "changed", default=[])) or "none"),
            "",
        ]
        for change in item["elements"]:
            lines += [
                f"### {change['change']}: `{change['tag_path']}`",
                "",
                f"- SHORT-NAME path: `{change['short_name_path'] or '-'}`; line {change['line']}",
            ]
            position = change.get("position")
            if position:
                after, before = position["after"], position["before"]
                lines.append(
                    f"- position: child {position['index'] + 1} of {position['of']}; after "
                    f"{_sibling(after)}; before {_sibling(before)}; siblings with this tag: "
                    f"{position['siblings_with_this_tag']}"
                )
            what = change.get("what")
            if what:
                for attribute in what.get("attributes", []):
                    lines.append(
                        f"- attribute {attribute['name']}: `{_cell(attribute['old_shape'])}` "
                        f"-> `{_cell(attribute['new_shape'])}` (shapes)"
                    )
                if "text" in what:
                    lines.append(
                        f"- text: `{what['text']['old_shape']}` -> `{what['text']['new_shape']}` "
                        "(shapes: letters as a, digits as 9)"
                    )
                if what.get("children_reordered"):
                    lines.append("- the order of its children changed")
            if "xml" in change:
                lines += [
                    f"- indentation of the first line: `{change['indentation']}` "
                    f"({change['xml_characters']} characters"
                    + (", cut" if change["xml_cut"] else "")
                    + (
                        ", exact source text"
                        if change["xml_is_exact_source_text"]
                        else ", re-written with tokens"
                    )
                    + ")",
                    "",
                    "```xml",
                    change["xml"].replace("\r\n", "\n").replace("\r", "\n"),
                    "```",
                ]
            lines.append("")
    return "\n".join(lines) + "\n"


def _sibling(value: Mapping[str, Any] | None) -> str:
    if value is None:
        return "nothing"
    name = f" `{value['short_name']}`" if value.get("short_name") else ""
    return f"{value['tag']}{name}"


# --------------------------------------------------------------------------- README, summary

_FILE_NOTES: tuple[tuple[str, str], ...] = (
    ("discovery.json", "complete machine-readable report of 'discover run'"),
    ("discovery.md", "readable summary of discovery.json (nothing that is not in the JSON)"),
    ("cli/", "what each Vector tool printed for each help flag, one file per attempt"),
    ("owners_proposed.csv", "proposal for owners.csv: SWCs marked davinci or model"),
    ("locked_proposed.txt", "SWCs proposed as locked (service or generated components)"),
    ("golden_diff_*.json / .md", "result of 'discover golden-diff' for one pair of commits"),
    ("manifest.json", "list of the reports in this folder and their mode"),
)


def render_readme(manifest: Mapping[str, Any], folder_name: str, private_name: str) -> str:
    """README.txt: which files to send back, what they contain, in which mode."""
    reports: Mapping[str, Mapping[str, Any]] = manifest.get("reports", {})
    modes = {bool(entry.get("anonymized")) for entry in reports.values()}
    lines = [
        "PortTool discovery report",
        "=========================",
        "",
        "WHAT TO DO",
        "1. Read discovery.md (and the cli folder) and check that you may share it.",
        f"2. Zip the whole folder '{folder_name}' and send the zip back.",
        f"3. Do NOT send the folder '{private_name}' if it exists. It holds the key and the",
        "   table that turn tokens back into real names. Keep it: we may ask you what a",
        "   token stands for.",
        "4. Do not commit either folder. Delete them when the exchange is finished.",
        "",
        "Nothing in the ECU repo was changed. No Vector tool was started with a project or",
        "workspace; the tools were started only with a help flag.",
        "",
        "FILES IN THIS FOLDER",
    ]
    for name, note in _FILE_NOTES:
        lines.append(f"  {name:<26} {note}")
    lines += ["", "REPORTS AND THEIR MODE"]
    for name in sorted(reports):
        entry = reports[name]
        mode = "ANONYMIZED" if entry.get("anonymized") else "NOT anonymized"
        lines.append(f"  {name}: {mode}, created {entry.get('created_utc')} (UTC)")
        for file_name in entry.get("files", []):
            lines.append(f"      {file_name}")
    if len(modes) > 1:
        lines += [
            "",
            "WARNING: this folder holds anonymized AND non-anonymized reports. If you want to",
            "send only anonymized data, delete the folder and run the commands again with",
            "--anonymize.",
        ]
    for anonymized in sorted(modes):
        lines += ["", "WHAT A REPORT OF THIS MODE CONTAINS"]
        lines += [_wrap(line) for line in mode_statement(anonymized, "all")]
    return "\n".join(lines) + "\n"


def _wrap(line: str, width: int = 86) -> str:
    words = line.split(" ")
    out: list[str] = []
    current = ""
    for word in words:
        if current and len(current) + 1 + len(word) > width:
            out.append(current)
            current = "  " + word
        else:
            current = f"{current} {word}" if current else word
    out.append(current)
    return "\n".join(out)


def _severity_line(report: Mapping[str, Any]) -> str:
    counts = report.get("findings_by_severity", {})
    return (
        f"Findings: {counts.get('error', 0)} errors, {counts.get('warning', 0)} warnings, "
        f"{counts.get('info', 0)} notes (listed in the report)"
    )


def _send_lines(out_dir: str, private_dir: str | None) -> list[str]:
    lines = [f"Report folder: {out_dir}", "Send back: the whole report folder (see README.txt)."]
    if private_dir is not None:
        lines.append(f"Do NOT send: {private_dir}")
    return lines


def render_summary(report: Mapping[str, Any], out_dir: str, private_dir: str | None) -> str:
    """The one-screen summary printed when ``discover run`` ends."""
    inventory = report.get("inventory", {})
    by_type = inventory.get("by_type", {})
    storage = report.get("storage_map", {})
    workspace = report.get("workspace", {})
    composition = report.get("composition", {})
    ecuc = _get(report, "ecuc", "summary", default={})
    owners = report.get("owners", {})
    git = report.get("git", {})
    tools = report.get("tools", {})
    found = sorted({item["tool"] for item in tools.get("executables", [])})
    if tools.get("skipped"):
        tool_line = "Tools: skipped (--no-tools)"
    else:
        tool_line = (
            f"Tools: found {', '.join(found) or 'none'}; not found "
            f"{', '.join(tools.get('not_found', [])) or 'none'}"
        )
    if not git.get("available"):
        git_line = "Git: not available"
    elif not git.get("is_repo"):
        git_line = "Git: the folder is not a git work tree"
    else:
        git_line = f"Git: {'clean' if git.get('clean') else 'NOT clean'} work tree"
    failed = [step["title"] for step in report.get("steps", []) if not step["ok"]]
    lines = [
        "",
        f"Discovery finished in {report.get('seconds')} s"
        + (" (ANONYMIZED report)." if _get(report, "mode", "anonymized") else "."),
        f"  ARXML files: {_get(by_type, 'arxml', 'count', default=0)} "
        f"({_megabytes(_get(by_type, 'arxml', 'bytes', default=0))}), with problems: "
        f"{len(inventory.get('problem_files', []))}",
        f"  Workspace files: {_get(workspace, 'dcf', 'found', default=0)} .dcf, "
        f"{_get(workspace, 'dpa', 'found', default=0)} .dpa",
        f"  File layout: {_counts(storage.get('file_layout'))}",
        f"  Compositions: {composition.get('compositions', 0)} (nested: "
        f"{_cell(composition.get('nested'))}), assembly connectors: "
        f"{composition.get('assembly_connectors', 0)}, delegation: "
        f"{composition.get('delegation_connectors', 0)}",
        f"  ECUC: Rte configurations {ecuc.get('rte_module_configurations', 0)}, events mapped "
        f"{ecuc.get('events_mapped_to_a_task', 0)}, Os tasks {ecuc.get('os_tasks', 0)}",
        f"  Owners proposal: {owners.get('proposed_model', 0)} model, "
        f"{owners.get('locked_candidates_total', 0)} locked, "
        f"{owners.get('proposed_davinci', 0)} davinci",
        f"  Lock or temporary files next to workspace files: "
        f"{_get(report, 'lock_signs', 'in_workspace_folders', default=0)}",
        "  " + git_line,
        "  " + tool_line,
        "  " + _severity_line(report),
    ]
    if failed:
        lines.append("  Steps that FAILED: " + "; ".join(failed))
    lines += _send_lines(out_dir, private_dir)
    return "\n".join(lines)


def render_golden_summary(
    report: Mapping[str, Any], out_dir: str, private_dir: str | None, stem: str
) -> str:
    summary = _get(report, "golden_diff", "summary", default={})
    lines = [
        "",
        f"Golden diff finished in {report.get('seconds')} s"
        + (" (ANONYMIZED report)." if _get(report, "mode", "anonymized") else "."),
        f"  Files changed: {summary.get('files_changed', 0)} (added {summary.get('added', 0)}, "
        f"deleted {summary.get('deleted', 0)}, modified {summary.get('modified', 0)})",
        f"  XML files compared: {summary.get('xml_files_compared', 0)}",
        f"  Elements added: {summary.get('elements_added', 0)}, removed: "
        f"{summary.get('elements_removed', 0)}, changed: {summary.get('elements_changed', 0)}",
        "  Files where only line endings changed: "
        f"{summary.get('files_with_only_line_ending_changes', 0)}",
        "  " + _severity_line(report),
        f"Report files: {stem}.json and {stem}.md",
    ]
    lines += _send_lines(out_dir, private_dir)
    return "\n".join(lines)
