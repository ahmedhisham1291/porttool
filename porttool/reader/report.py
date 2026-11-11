"""Plain-text views of a model for the command line: rows, tables, summaries.

Pure functions over ``Model``. The click commands in ``cli.py`` only parse arguments and
print what these return.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence

from porttool.core.models import ConnectorEnd, Model
from porttool.core.results import Finding, Severity
from porttool.reader.reader import LoadStats

Row = tuple[str, ...]

SWC_HEADERS: Row = ("SWC", "Kind", "ARXML owner", "Team", "Ports", "Runnables", "Path", "File")
PORT_HEADERS: Row = ("SWC", "Port", "Direction", "Interface", "Interface kind", "Port path")
CONNECTOR_HEADERS: Row = ("Composition", "Connector", "Kind", "Provider", "Requester")
UNCONNECTED_HEADERS: Row = ("Composition", "Prototype", "Port", "Direction", "Interface")

_OUTER = "(composition)"


def _name(path: str | None) -> str:
    return "" if path is None else path.rpartition("/")[2]


def swc_rows(model: Model) -> list[Row]:
    """One row per component type, sorted by name then path."""
    rows = [
        (
            c.name,
            c.kind.value,
            c.owner.value,
            c.team or "",
            str(len(model.ports_of(c.path))),
            str(len(model.runnables_of(c.path))),
            c.path,
            c.origin.file,
        )
        for c in model.components.values()
    ]
    return sorted(rows, key=lambda row: (row[0], row[6]))


def port_rows(model: Model, swc: str | None = None) -> list[Row]:
    """One row per port of a component type. ``swc`` filters by short name or path."""
    rows: list[Row] = []
    for port in model.ports.values():
        component = model.components.get(port.swc_path)
        swc_name = component.name if component is not None else _name(port.swc_path)
        if swc is not None and swc not in (swc_name, port.swc_path):
            continue
        interface = (
            None if port.interface_path is None else model.interfaces.get(port.interface_path)
        )
        rows.append(
            (
                swc_name,
                port.name,
                port.direction.value,
                port.interface_path or "",
                interface.kind.value if interface is not None else "",
                port.path,
            )
        )
    return sorted(rows, key=lambda row: (row[0], row[1], row[5]))


def _end(end: ConnectorEnd) -> str:
    owner = _OUTER if end.prototype_path is None else _name(end.prototype_path)
    return f"{owner}.{_name(end.port_path)}" if end.port_path is not None else f"{owner}.?"


def connector_rows(model: Model) -> list[Row]:
    rows = [
        (
            _name(c.composition_path),
            c.name,
            c.kind.value,
            _end(c.provider),
            _end(c.requester),
        )
        for c in model.connectors.values()
    ]
    return sorted(rows)


def unconnected_rows(model: Model) -> list[Row]:
    """One row per port instance that no connector is attached to."""
    rows = [
        (
            _name(instance.prototype.composition_path),
            instance.prototype.name,
            instance.port.name,
            instance.port.direction.value,
            instance.port.interface_path or "",
        )
        for instance in model.unconnected()
    ]
    return sorted(rows)


def format_table(headers: Row, rows: Sequence[Row]) -> str:
    """Left-aligned columns separated by two spaces, with a header and a rule."""
    widths = [len(h) for h in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    lines = [
        "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(headers)).rstrip(),
        "  ".join("-" * width for width in widths),
    ]
    lines.extend(
        "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip() for row in rows
    )
    return "\n".join(lines)


def format_tsv(headers: Row, rows: Sequence[Row]) -> str:
    """Tab-separated values with a header line. Tabs and line breaks in cells become spaces."""

    def clean(cell: str) -> str:
        return cell.replace("\t", " ").replace("\r", " ").replace("\n", " ")

    return "\n".join("\t".join(clean(cell) for cell in row) for row in (headers, *rows))


def count_by_severity(findings: Iterable[Finding]) -> dict[Severity, int]:
    counts = Counter(f.severity for f in findings)
    return {severity: counts.get(severity, 0) for severity in Severity}


def has_errors(model: Model) -> bool:
    return any(f.severity is Severity.ERROR for f in model.findings)


def summary_lines(model: Model, stats: LoadStats | None = None) -> list[str]:
    """What was loaded, in a few lines."""
    counts = count_by_severity(model.findings)
    lines = [
        f"Components: {len(model.components)} ({len(model.compositions)} compositions)",
        f"Ports: {len(model.ports)}",
        f"Interfaces: {len(model.interfaces)}",
        f"Runnables: {len(model.runnables)}, access points: {len(model.access_points)}, "
        f"triggers: {len(model.triggers)}",
        f"Component prototypes: {len(model.prototypes)}, connectors: {len(model.connectors)}, "
        f"unconnected port instances: {len(model.unconnected())}",
        f"Data types: {len(model.data_types)}, base types: {len(model.base_types)}, "
        f"compu methods: {len(model.compu_methods)}, constants: {len(model.constants)}",
        f"OS tasks: {len(model.tasks)}, mapped events: {len(model.task_mappings)}",
        f"Findings: {counts[Severity.ERROR]} errors, {counts[Severity.WARNING]} warnings, "
        f"{counts[Severity.INFO]} notes",
    ]
    if stats is not None:
        megabytes = stats.bytes_total / (1024 * 1024)
        lines.append(
            f"Files: {stats.files_total} ({megabytes:.1f} MB), parsed {stats.files_parsed}, "
            f"from cache {stats.files_from_cache}, not loaded {stats.files_failed}"
        )
        lines.append(
            f"Time: {stats.seconds_total:.2f} s (scan {stats.seconds_scan:.2f}, "
            f"cache read {stats.seconds_cache_read:.2f}, parse {stats.seconds_parse:.2f}, "
            f"cache write {stats.seconds_cache_write:.2f}, build {stats.seconds_build:.2f})"
        )
        if stats.schemas:
            lines.append("Schema: " + ", ".join(stats.schemas))
    return lines


def finding_lines(findings: Sequence[Finding], limit: int | None = None) -> list[str]:
    """One line per finding, errors first. ``limit`` cuts the list and says how many are left."""
    order = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}
    ranked = sorted(findings, key=lambda f: order[f.severity])
    shown = ranked if limit is None else ranked[:limit]
    lines = []
    for finding in shown:
        where = finding.file or finding.object_path or "-"
        lines.append(
            f"{finding.severity.value.upper():7} {finding.code} {where}: {finding.message}"
        )
    if len(shown) < len(ranked):
        lines.append(f"... and {len(ranked) - len(shown)} more findings")
    return lines
