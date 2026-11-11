"""Model -> rows of every data sheet. Pure functions: no file access, no XlsxWriter.

A row is a tuple aligned with ``porttool.core.sheets.COLUMNS[sheet]``. A cell is text, a
number, or ``""`` for an empty cell. How each cell text is built is documented in
``porttool/workbook/HANDOFF.md`` ("Cell text formats"), because the validator sees the
same texts in ``RawRequestRow.values``.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import PureWindowsPath
from typing import Final

from porttool.core.config import ProjectConfig
from porttool.core.models import (
    ArxmlOwner,
    Component,
    DataType,
    Model,
    PortDirection,
    PortInstance,
    PortInterface,
)
from porttool.core.results import RequestResult
from porttool.core.rowid import interface_element_key, row_id, unconnected_key
from porttool.core.sheets import COLUMNS, Sheet

CellValue = str | int | float
Row = tuple[CellValue, ...]

YES: Final = "Y"
NO: Final = "N"

#: Separator between the entries of a list cell (enumeration values, record elements, ...).
ITEM_SEPARATOR: Final = ";"
#: Separator between the names in the "Compatible partner ports" cell.
PARTNER_SEPARATOR: Final = "; "

_MAX_EXACT_INT: Final = 2**53

#: Which directions a port of the key direction can be connected to.
_OPPOSITE: Final[Mapping[PortDirection, tuple[PortDirection, ...]]] = {
    PortDirection.P: (PortDirection.R, PortDirection.PR),
    PortDirection.R: (PortDirection.P, PortDirection.PR),
    PortDirection.PR: (PortDirection.P, PortDirection.R, PortDirection.PR),
}


@dataclass(frozen=True, slots=True)
class SheetRows:
    """Rows of one sheet plus, per row, the AUTOSAR path of the object it shows."""

    sheet: Sheet
    rows: tuple[Row, ...]
    paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Labels:
    """Display text per object path: the short name, or the path where the name repeats."""

    components: Mapping[str, str]
    interfaces: Mapping[str, str]
    data_types: Mapping[str, str]
    base_types: Mapping[str, str]
    units: Mapping[str, str]
    compositions: Mapping[str, str]


def _unique_labels(objects: Iterable[tuple[str, str]]) -> dict[str, str]:
    """``(path, name)`` pairs -> ``{path: name}``; a repeated name is replaced by its path."""
    pairs = list(objects)
    counts = Counter(name for _, name in pairs)
    return {path: (name if counts[name] == 1 else path) for path, name in pairs}


def build_labels(model: Model) -> Labels:
    return Labels(
        components=_unique_labels((c.path, c.name) for c in model.components.values()),
        interfaces=_unique_labels((i.path, i.name) for i in model.interfaces.values()),
        data_types=_unique_labels((d.path, d.name) for d in model.data_types.values()),
        base_types=_unique_labels((b.path, b.name) for b in model.base_types.values()),
        units=_unique_labels((u.path, u.name) for u in model.units.values()),
        compositions=_unique_labels((c.path, c.name) for c in model.compositions.values()),
    )


def _ref(labels: Mapping[str, str], path: str | None) -> str:
    """Label of a referenced object; a dangling reference shows its path."""
    if path is None:
        return ""
    return labels.get(path, path)


def _last(path: str | None) -> str:
    return "" if path is None else path.rsplit("/", 1)[-1]


def number_cell(value: float | None) -> CellValue:
    """A model number as a cell: int when integral, text for inf/nan, ``""`` for None."""
    if value is None:
        return ""
    if isinstance(value, int):
        return value
    if math.isnan(value) or math.isinf(value):
        return repr(value)
    if value.is_integer() and abs(value) < _MAX_EXACT_INT:
        return int(value)
    return value


def period_ms_cell(period_s: float | None) -> CellValue:
    """Seconds -> milliseconds without binary noise (0.007 s is 7 ms, not 7.000000000000001)."""
    if period_s is None:
        return ""
    return number_cell(round(period_s * 1000.0, 6))


def _sheet_rows(sheet: Sheet, entries: Iterable[tuple[str, Mapping[str, CellValue]]]) -> SheetRows:
    """Order every entry's cells like the layout contract; unknown layout keys stay empty."""
    rows: list[Row] = []
    paths: list[str] = []
    keys = [column.key for column in COLUMNS[sheet]]
    for path, cells in entries:
        rows.append(tuple([cells.get(key, "") for key in keys]))
        paths.append(path)
    return SheetRows(sheet=sheet, rows=tuple(rows), paths=tuple(paths))


# --------------------------------------------------------------------------- sheets


def _component_owner(component: Component, config: ProjectConfig) -> ArxmlOwner:
    """The stricter of what the reader stored and what the configuration says."""
    if component.owner is not ArxmlOwner.DAVINCI:
        return component.owner
    return config.owner_of(component.name)


def component_rows(model: Model, config: ProjectConfig) -> SheetRows:
    def entries() -> Iterable[tuple[str, Mapping[str, CellValue]]]:
        for component in sorted(model.components.values(), key=lambda c: (c.name, c.path)):
            team = component.team if component.team is not None else config.team_of(component.name)
            yield (
                component.path,
                {
                    "name": component.name,
                    "kind": component.kind.value,
                    "package_path": component.path.rsplit("/", 1)[0],
                    "source_file": component.origin.file,
                    "team": team or "",
                    "arxml_owner": _component_owner(component, config).value,
                },
            )

    return _sheet_rows(Sheet.COMPONENTS, entries())


def _data_type_cells(model: Model, labels: Labels, data_type: DataType) -> dict[str, CellValue]:
    compu = model.compu_methods.get(data_type.compu_method_path or "")
    constraint = model.constraints.get(data_type.constraint_path or "")
    unit_path = data_type.unit_path
    if unit_path is None and compu is not None:
        unit_path = compu.unit_path
    element_type = ""
    record_elements = ""
    if data_type.element_names:
        record_elements = ITEM_SEPARATOR.join(
            f"{name}:{_ref(labels.data_types, type_path)}"
            for name, type_path in zip(
                data_type.element_names, data_type.element_type_paths, strict=False
            )
        )
    elif data_type.element_type_paths:
        element_type = _ref(labels.data_types, data_type.element_type_paths[0])
    enum_values = ""
    if compu is not None and compu.enum_values:
        enum_values = ITEM_SEPARATOR.join(f"{value}={text}" for value, text in compu.enum_values)
    return {
        "row_id": row_id(Sheet.DATA_TYPES, data_type.path),
        "name": data_type.name,
        "level": data_type.level.value,
        "category": data_type.category.value,
        "base_type": _ref(labels.base_types, data_type.base_type_path),
        "lower_limit": number_cell(constraint.lower if constraint is not None else None),
        "upper_limit": number_cell(constraint.upper if constraint is not None else None),
        "unit": _ref(labels.units, unit_path),
        "factor": number_cell(compu.factor if compu is not None else None),
        "offset": number_cell(compu.offset if compu is not None else None),
        "enum_values": enum_values,
        "element_type": element_type,
        "array_size": number_cell(data_type.array_size),
        "record_elements": record_elements,
    }


def data_type_rows(model: Model, labels: Labels) -> SheetRows:
    ordered = sorted(model.data_types.values(), key=lambda d: (d.name, d.path))
    return _sheet_rows(
        Sheet.DATA_TYPES, ((d.path, _data_type_cells(model, labels, d)) for d in ordered)
    )


def _sorted_interfaces(model: Model) -> list[PortInterface]:
    return sorted(model.interfaces.values(), key=lambda i: (i.name, i.path))


def interface_rows(model: Model) -> SheetRows:
    def entries() -> Iterable[tuple[str, Mapping[str, CellValue]]]:
        for interface in _sorted_interfaces(model):
            yield (
                interface.path,
                {
                    "row_id": row_id(Sheet.INTERFACES, interface.path),
                    "name": interface.name,
                    "kind": interface.kind.value,
                },
            )

    return _sheet_rows(Sheet.INTERFACES, entries())


def interface_element_rows(model: Model, labels: Labels) -> SheetRows:
    """One row per data element; per client-server operation one row per argument."""

    def cells(
        interface: PortInterface,
        element: str,
        argument: str = "",
        direction: str = "",
        type_path: str | None = None,
        queued: str = "",
    ) -> tuple[str, Mapping[str, CellValue]]:
        key = interface_element_key(interface.path, element, argument)
        return (
            interface.path,
            {
                "row_id": row_id(Sheet.INTERFACE_ELEMENTS, key),
                "interface": labels.interfaces.get(interface.path, interface.path),
                "element": element,
                "argument": argument,
                "direction": direction,
                "data_type": _ref(labels.data_types, type_path),
                "queued": queued,
            },
        )

    def entries() -> Iterable[tuple[str, Mapping[str, CellValue]]]:
        for interface in _sorted_interfaces(model):
            for element in interface.elements:
                yield cells(
                    interface,
                    element.name,
                    type_path=element.type_path,
                    queued=YES if element.is_queued else NO,
                )
            for operation in interface.operations:
                if not operation.arguments:
                    yield cells(interface, operation.name)
                for argument in operation.arguments:
                    yield cells(
                        interface,
                        operation.name,
                        argument=argument.name,
                        direction=argument.direction.value,
                        type_path=argument.type_path,
                    )

    return _sheet_rows(Sheet.INTERFACE_ELEMENTS, entries())


def _swc_sort_key(model: Model, swc_path: str) -> tuple[str, str]:
    component = model.components.get(swc_path)
    return (component.name if component is not None else _last(swc_path), swc_path)


def _swc_label(labels: Labels, swc_path: str) -> str:
    return labels.components.get(swc_path, swc_path)


def init_value_text(pairs: Iterable[tuple[str, str | None]]) -> str:
    """``element=value`` per element that has an init value, joined with ``;``."""
    return ITEM_SEPARATOR.join(f"{target}={value}" for target, value in pairs if value is not None)


def port_rows(model: Model, labels: Labels) -> SheetRows:
    """One row per port of an SWC type. Connection state is summed over its prototypes."""

    def entries() -> Iterable[tuple[str, Mapping[str, CellValue]]]:
        ordered = sorted(
            model.ports.values(),
            key=lambda p: (*_swc_sort_key(model, p.swc_path), p.name, p.path),
        )
        for port in ordered:
            partner_count = sum(
                len(model.connectors_at(prototype.path, port.path))
                for prototype in model.prototypes_of_type(port.swc_path)
            )
            interface = model.interfaces.get(port.interface_path or "")
            yield (
                port.path,
                {
                    "row_id": row_id(Sheet.PORTS, port.path),
                    "swc": _swc_label(labels, port.swc_path),
                    "port": port.name,
                    "direction": port.direction.value,
                    "interface": _ref(labels.interfaces, port.interface_path),
                    "interface_kind": interface.kind.value if interface is not None else "",
                    "connected": YES if partner_count else NO,
                    "partner_count": partner_count,
                    "init_value": init_value_text(
                        (spec.target, spec.init_value) for spec in port.com_specs
                    ),
                },
            )

    return _sheet_rows(Sheet.PORTS, entries())


def _runnable_name(model: Model, runnable_path: str | None) -> str:
    runnable = model.runnables.get(runnable_path or "")
    return runnable.name if runnable is not None else _last(runnable_path)


def _port_name(model: Model, port_path: str | None) -> str:
    port = model.ports.get(port_path or "")
    return port.name if port is not None else _last(port_path)


def runnable_access_rows(model: Model, labels: Labels) -> SheetRows:
    def entries() -> Iterable[tuple[str, Mapping[str, CellValue]]]:
        keyed = [
            (
                (
                    *_swc_sort_key(model, access.swc_path),
                    _runnable_name(model, access.runnable_path),
                    _port_name(model, access.port_path),
                    access.target or "",
                    access.path,
                ),
                access,
            )
            for access in model.access_points.values()
        ]
        keyed.sort(key=lambda item: item[0])
        for key, access in keyed:
            yield (
                access.path,
                {
                    "row_id": row_id(Sheet.RUNNABLE_ACCESS, access.path),
                    "swc": _swc_label(labels, access.swc_path),
                    "runnable": key[2],
                    "port": key[3],
                    "target": key[4],
                    "access_kind": access.kind.value,
                },
            )

    return _sheet_rows(Sheet.RUNNABLE_ACCESS, entries())


def runnable_trigger_rows(model: Model, labels: Labels) -> SheetRows:
    def entries() -> Iterable[tuple[str, Mapping[str, CellValue]]]:
        keyed = [
            (
                (
                    *_swc_sort_key(model, trigger.swc_path),
                    _runnable_name(model, trigger.runnable_path),
                    trigger.name,
                    trigger.path,
                ),
                trigger,
            )
            for trigger in model.triggers.values()
        ]
        keyed.sort(key=lambda item: item[0])
        for key, trigger in keyed:
            mapping = model.task_of(trigger.path)
            yield (
                trigger.path,
                {
                    "row_id": row_id(Sheet.RUNNABLE_TRIGGERS, trigger.path),
                    "swc": _swc_label(labels, trigger.swc_path),
                    "runnable": key[2],
                    "trigger_kind": trigger.kind.value,
                    "port": _port_name(model, trigger.port_path),
                    "target": trigger.target or "",
                    "period_ms": period_ms_cell(trigger.period_s),
                    "task": mapping.task if mapping is not None else "",
                },
            )

    return _sheet_rows(Sheet.RUNNABLE_TRIGGERS, entries())


def _prototype_name(model: Model, prototype_path: str | None) -> str:
    prototype = model.prototypes.get(prototype_path or "")
    return prototype.name if prototype is not None else _last(prototype_path)


def _composition_label(labels: Labels, composition_path: str) -> str:
    label = labels.compositions.get(composition_path)
    if label is not None:
        return label
    return labels.components.get(composition_path, composition_path)


def connection_rows(model: Model, labels: Labels) -> SheetRows:
    """One row per connector. The SWC columns hold prototype names (empty: delegation end)."""

    def entries() -> Iterable[tuple[str, Mapping[str, CellValue]]]:
        ordered = sorted(
            model.connectors.values(),
            key=lambda c: (_last(c.composition_path), c.composition_path, c.name, c.path),
        )
        for connector in ordered:
            yield (
                connector.path,
                {
                    "row_id": row_id(Sheet.CONNECTIONS, connector.path),
                    "provider_swc": _prototype_name(model, connector.provider.prototype_path),
                    "provider_port": _port_name(model, connector.provider.port_path),
                    "requester_swc": _prototype_name(model, connector.requester.prototype_path),
                    "requester_port": _port_name(model, connector.requester.port_path),
                    "composition": _composition_label(labels, connector.composition_path),
                    "connector_name": connector.name,
                },
            )

    return _sheet_rows(Sheet.CONNECTIONS, entries())


def _instance_sort_key(instance: PortInstance) -> tuple[str, str, str, str]:
    return (
        instance.prototype.name,
        instance.port.name,
        instance.prototype.path,
        instance.port.path,
    )


def _instance_name(instance: PortInstance) -> str:
    return f"{instance.prototype.name}.{instance.port.name}"


def unconnected_rows(model: Model, labels: Labels, partner_limit: int) -> SheetRows:
    """Unconnected port instances with the ports they could be connected to.

    A compatible partner sits in the same composition, uses the same interface and has
    the opposite direction (PR matches every direction). At most ``partner_limit`` names
    are listed; the rest is counted as ``(+N more)``.
    """
    by_slot: dict[tuple[str, str, PortDirection], list[PortInstance]] = defaultdict(list)
    for instance in model.port_instances():
        interface_path = instance.port.interface_path
        if interface_path is not None:
            slot = (instance.prototype.composition_path, interface_path, instance.port.direction)
            by_slot[slot].append(instance)
    for instances in by_slot.values():
        instances.sort(key=_instance_sort_key)

    def partners(instance: PortInstance) -> str:
        interface_path = instance.port.interface_path
        if interface_path is None:
            return ""
        total = 0
        head: list[PortInstance] = []
        for direction in _OPPOSITE[instance.port.direction]:
            slot = (instance.prototype.composition_path, interface_path, direction)
            candidates = by_slot.get(slot, [])
            total += len(candidates)
            head.extend(candidates[: partner_limit + 1])
        if instance.port.direction is PortDirection.PR:
            total -= 1  # the instance itself sits in the PR slot
            head = [other for other in head if other.key != instance.key]
        head.sort(key=_instance_sort_key)
        names = [_instance_name(other) for other in head[:partner_limit]]
        text = PARTNER_SEPARATOR.join(names)
        if total > len(names):
            more = f"(+{total - len(names)} more)"
            text = f"{text} {more}" if text else more
        return text

    def entries() -> Iterable[tuple[str, Mapping[str, CellValue]]]:
        for instance in sorted(model.unconnected(), key=_instance_sort_key):
            key = unconnected_key(instance.prototype.path, instance.port.path)
            yield (
                instance.port.path,
                {
                    "row_id": row_id(Sheet.UNCONNECTED, key),
                    "swc": instance.prototype.name,
                    "port": instance.port.name,
                    "direction": instance.port.direction.value,
                    "interface": _ref(labels.interfaces, instance.port.interface_path),
                    "compatible_partners": partners(instance),
                },
            )

    return _sheet_rows(Sheet.UNCONNECTED, entries())


def result_rows(results: Sequence[RequestResult]) -> SheetRows:
    """One row per finding of each result; a result without findings still gets one row.

    ``RequestResult`` carries no RowID, so the RowID column shows ``request_id``. The
    workbook file and row of the request are appended to the reason, because the layout
    has no column for them.
    """

    def entries() -> Iterable[tuple[str, Mapping[str, CellValue]]]:
        for result in results:
            sheet_name = ""
            where = ""
            if result.row is not None:
                sheet_name = result.row.sheet
                file_name = PureWindowsPath(result.row.file).name
                where = f"[{file_name}, row {result.row.row}]"
            base: dict[str, CellValue] = {
                "row_id": result.request_id,
                "sheet": sheet_name,
                "status": result.status.value,
            }
            if not result.findings:
                yield ("", {**base, "code": "", "reason": where})
            for finding in result.findings:
                reason = f"{finding.message} {where}" if where else finding.message
                yield (
                    finding.object_path or "",
                    {**base, "code": finding.code, "reason": reason},
                )

    return _sheet_rows(Sheet.RESULTS, entries())


def build_all_rows(
    model: Model,
    config: ProjectConfig,
    results: Sequence[RequestResult],
    labels: Labels,
    partner_limit: int,
) -> dict[Sheet, SheetRows]:
    """Rows of every sheet that has a column layout, in no particular order."""
    return {
        Sheet.COMPONENTS: component_rows(model, config),
        Sheet.DATA_TYPES: data_type_rows(model, labels),
        Sheet.INTERFACES: interface_rows(model),
        Sheet.INTERFACE_ELEMENTS: interface_element_rows(model, labels),
        Sheet.PORTS: port_rows(model, labels),
        Sheet.RUNNABLE_ACCESS: runnable_access_rows(model, labels),
        Sheet.RUNNABLE_TRIGGERS: runnable_trigger_rows(model, labels),
        Sheet.CONNECTIONS: connection_rows(model, labels),
        Sheet.UNCONNECTED: unconnected_rows(model, labels, partner_limit),
        Sheet.RESULTS: result_rows(results),
    }
