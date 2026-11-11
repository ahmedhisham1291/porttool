"""Table data for the web page.

The rows of every table are built once per loaded model (in a worker thread, see
``porttool.gui.server``). After that a request only filters, sorts and slices lists that
are already in memory.

The column keys and headers come from ``porttool.core.sheets`` so a table shows the same
columns as the sheet of the same name in the workbook. Values are text. The naming
conventions mirror the workbook: an object is shown by its short name, or by its path
where the short name repeats.
"""

from __future__ import annotations

import math
import threading
from collections import Counter, OrderedDict, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from porttool.core.config import ProjectConfig
from porttool.core.models import (
    AccessKind,
    ArgumentDirection,
    DataTypeCategory,
    DataTypeLevel,
    InterfaceKind,
    Model,
    PortDirection,
    PortInstance,
    TriggerKind,
)
from porttool.core.pending import PendingQueue
from porttool.core.rowid import interface_element_key, row_id, unconnected_key
from porttool.core.sheets import COLUMNS, Action, Sheet

#: Most rows one call returns.
MAX_LIMIT = 500
DEFAULT_LIMIT = 200
YES = "Y"
NO = "N"
ITEM_SEPARATOR = ";"
PARTNER_SEPARATOR = "; "
_FIELD_SEPARATOR = "\x1f"  # between the cells of the per-row search text
_REQUEST_KEYS = frozenset({"action", "requester", "comment"})
_NUMERIC_KEYS = frozenset(
    {"partner_count", "period_ms", "array_size", "lower_limit", "upper_limit", "factor", "offset"}
)
_WIDTHS: Mapping[str, int] = {
    "row_id": 150,
    "direction": 100,
    "connected": 130,
    "partner_count": 100,
    "kind": 150,
    "level": 130,
    "category": 130,
    "queued": 120,
    "period_ms": 110,
    "array_size": 100,
    "lower_limit": 110,
    "upper_limit": 110,
    "factor": 90,
    "offset": 90,
    "package_path": 240,
    "source_file": 320,
    "compatible_partners": 420,
    "interface": 220,
    "connector_name": 300,
    "depends_on": 300,
    "note": 300,
    "object": 320,
}
_DEFAULT_WIDTH = 180

_OPPOSITE: Mapping[PortDirection, tuple[PortDirection, ...]] = {
    PortDirection.P: (PortDirection.R, PortDirection.PR),
    PortDirection.R: (PortDirection.P, PortDirection.PR),
    PortDirection.PR: (PortDirection.P, PortDirection.R, PortDirection.PR),
}


class QueryError(ValueError):
    """A table query that cannot be answered (unknown column, bad number)."""


@dataclass(frozen=True, slots=True)
class ColumnDef:
    key: str
    header: str
    width: int  # CSS pixels
    numeric: bool = False


@dataclass(frozen=True, slots=True)
class TableDef:
    name: str
    title: str
    columns: tuple[ColumnDef, ...]
    only: tuple[str, ...] = ()  # values accepted by the ``only`` parameter

    def column_index(self, key: str) -> int:
        for index, column in enumerate(self.columns):
            if column.key == key:
                return index
        raise QueryError(f"Table '{self.name}' has no column '{key}'.")


def _column(key: str, header: str) -> ColumnDef:
    return ColumnDef(key, header, _WIDTHS.get(key, _DEFAULT_WIDTH), key in _NUMERIC_KEYS)


def _sheet_table(name: str, title: str, sheet: Sheet, only: tuple[str, ...] = ()) -> TableDef:
    """Columns of the sheet without the request columns; the RowID moves to the end."""
    shown = [c for c in COLUMNS[sheet] if c.key not in _REQUEST_KEYS and c.key != "row_id"]
    shown += [c for c in COLUMNS[sheet] if c.key == "row_id"]
    return TableDef(name, title, tuple(_column(c.key, c.header) for c in shown), only)


TABLE_DEFS: tuple[TableDef, ...] = (
    _sheet_table("components", "Components", Sheet.COMPONENTS),
    _sheet_table("ports", "Ports", Sheet.PORTS, only=("unconnected",)),
    _sheet_table("runnable_access", "Runnable access", Sheet.RUNNABLE_ACCESS),
    _sheet_table("triggers", "Triggers", Sheet.RUNNABLE_TRIGGERS),
    _sheet_table("connections", "Connections", Sheet.CONNECTIONS),
    _sheet_table("unconnected", "Unconnected", Sheet.UNCONNECTED),
    _sheet_table("interfaces", "Interfaces", Sheet.INTERFACES),
    _sheet_table("interface_elements", "Interface elements", Sheet.INTERFACE_ELEMENTS),
    _sheet_table("data_types", "Data types", Sheet.DATA_TYPES),
    TableDef(
        "pending",
        "Pending",
        (
            _column("state", "State"),
            _column("operation", "Operation"),
            _column("object", "Object"),
            _column("requester", "Requester"),
            _column("created_utc", "Created (UTC)"),
            _column("owner_team", "Owner team"),
            _column("depends_on", "Waits for"),
            _column("note", "Note"),
        ),
    ),
)
TABLE_DEF_BY_NAME: Mapping[str, TableDef] = {table.name: table for table in TABLE_DEFS}


# --------------------------------------------------------------------------- queries


@dataclass(frozen=True, slots=True)
class Query:
    offset: int = 0
    limit: int = DEFAULT_LIMIT
    sort: str | None = None
    descending: bool = False
    search: str = ""  # lower case
    filters: tuple[tuple[str, str], ...] = ()  # (column key, lower-case text), sorted by key
    only: str | None = None


@dataclass(frozen=True, slots=True)
class Page:
    rows: list[tuple[str, ...]]
    total: int
    filtered: int
    offset: int
    limit: int


def _one(params: Mapping[str, Sequence[str]], name: str) -> str | None:
    values = params.get(name)
    return values[-1] if values else None


def _int_param(params: Mapping[str, Sequence[str]], name: str, default: int) -> int:
    text = _one(params, name)
    if text is None or text == "":
        return default
    try:
        value = int(text)
    except ValueError:
        raise QueryError(f"'{name}' must be a whole number, not '{text[:40]}'.") from None
    if value < 0:
        raise QueryError(f"'{name}' must not be negative.")
    return value


def _clean(text: str) -> str:
    """Search text as it is compared: lower case, no control characters, trimmed."""
    return "".join(ch for ch in text if ch >= " ").strip().lower()


def parse_query(table: TableDef, params: Mapping[str, Sequence[str]]) -> Query:
    """Turn URL parameters into a ``Query``. Raises ``QueryError`` for anything unknown."""
    offset = _int_param(params, "offset", 0)
    limit = min(_int_param(params, "limit", DEFAULT_LIMIT), MAX_LIMIT)
    sort = _one(params, "sort") or None
    if sort is not None:
        table.column_index(sort)
    direction = (_one(params, "dir") or "asc").lower()
    if direction not in ("asc", "desc"):
        raise QueryError("'dir' must be 'asc' or 'desc'.")
    only = _one(params, "only") or None
    if only is not None and only not in table.only:
        raise QueryError(f"Table '{table.name}' does not support only={only[:40]}.")
    filters: dict[str, str] = {}
    for name, values in params.items():
        if not name.startswith("f."):
            continue
        key = name[2:]
        table.column_index(key)
        text = _clean(values[-1]) if values else ""
        if text:
            filters[key] = text
    return Query(
        offset=offset,
        limit=limit,
        sort=sort,
        descending=direction == "desc",
        search=_clean(_one(params, "q") or ""),
        filters=tuple(sorted(filters.items())),
        only=only,
    )


def _numeric_key(text: str, lowered: str) -> tuple[int, float, str]:
    try:
        number = float(text)
    except ValueError:
        return (1, 0.0, lowered)
    if math.isnan(number):
        return (1, 0.0, lowered)
    return (0, number, "")


class TableData:
    """The rows of one table plus what is needed to filter and sort them quickly.

    Thread-safe: the rows never change; the lazily built sort orders and the small cache
    of query results are guarded by a lock.
    """

    _RESULT_CACHE_SIZE = 8

    def __init__(self, definition: TableDef, rows: Iterable[Sequence[str]]) -> None:
        self.definition = definition
        self.rows: list[tuple[str, ...]] = [tuple(row) for row in rows]
        lowered: dict[str, str] = {}
        self._lower: list[tuple[str, ...]] = []
        self._joined: list[str] = []
        for row in self.rows:
            low_row: list[str] = []
            for cell in row:
                low = lowered.get(cell)
                if low is None:
                    low = cell.lower()
                    lowered[cell] = low
                low_row.append(low)
            self._lower.append(tuple(low_row))
            self._joined.append(_FIELD_SEPARATOR.join(low_row))
        self._lock = threading.Lock()
        self._orders: dict[tuple[int, bool], list[int]] = {}
        self._results: OrderedDict[tuple[object, ...], Sequence[int]] = OrderedDict()

    def __len__(self) -> int:
        return len(self.rows)

    def _order(self, column: int, descending: bool) -> list[int]:
        key = (column, descending)
        with self._lock:
            order = self._orders.get(key)
        if order is not None:
            return order
        lower = self._lower
        if self.definition.columns[column].numeric:
            rows = self.rows
            order = sorted(
                range(len(rows)),
                key=lambda i: _numeric_key(rows[i][column], lower[i][column]),
                reverse=descending,
            )
        else:
            order = sorted(range(len(lower)), key=lambda i: lower[i][column], reverse=descending)
        with self._lock:
            self._orders[key] = order
        return order

    def _matching(self, query: Query) -> Sequence[int]:
        key = (query.sort, query.descending, query.search, query.filters, query.only)
        with self._lock:
            cached = self._results.get(key)
            if cached is not None:
                self._results.move_to_end(key)
                return cached
        definition = self.definition
        order: Sequence[int]
        if query.sort is None:
            order = range(len(self.rows))
        else:
            order = self._order(definition.column_index(query.sort), query.descending)
        lower = self._lower
        tests: list[tuple[int, str]] = [
            (definition.column_index(column), text) for column, text in query.filters
        ]
        result: Sequence[int] = order
        if query.only == "unconnected":
            connected = definition.column_index("connected")
            unconnected = NO.lower()
            result = [i for i in result if lower[i][connected] == unconnected]
        if query.search:
            joined = self._joined
            search = query.search
            result = [i for i in result if search in joined[i]]
        for column, text in tests:
            result = [i for i in result if text in lower[i][column]]
        with self._lock:
            self._results[key] = result
            while len(self._results) > self._RESULT_CACHE_SIZE:
                self._results.popitem(last=False)
        return result

    def query(self, query: Query) -> Page:
        matching = self._matching(query)
        chosen = matching[query.offset : query.offset + query.limit]
        rows = self.rows
        return Page(
            rows=[rows[i] for i in chosen],
            total=len(rows),
            filtered=len(matching),
            offset=query.offset,
            limit=query.limit,
        )


# --------------------------------------------------------------------------- row builders


def _unique_labels(objects: Iterable[tuple[str, str]]) -> dict[str, str]:
    """``(path, name)`` pairs -> ``{path: name}``; a repeated name is replaced by its path."""
    pairs = list(objects)
    counts = Counter(name for _, name in pairs)
    return {path: (name if counts[name] == 1 else path) for path, name in pairs}


@dataclass(frozen=True, slots=True)
class Labels:
    components: Mapping[str, str]
    interfaces: Mapping[str, str]
    data_types: Mapping[str, str]
    base_types: Mapping[str, str]
    units: Mapping[str, str]
    compositions: Mapping[str, str]


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
    return "" if path is None else labels.get(path, path)


def _last(path: str | None) -> str:
    return "" if path is None else path.rsplit("/", 1)[-1]


def _number(value: float | None) -> str:
    if value is None:
        return ""
    if isinstance(value, int):
        return str(value)
    if math.isnan(value) or math.isinf(value):
        return repr(value)
    if value.is_integer() and abs(value) < 2**53:
        return str(int(value))
    return repr(value)


def _period_ms(period_s: float | None) -> str:
    return "" if period_s is None else _number(round(period_s * 1000.0, 6))


def _ordered(definition: TableDef, cells: Mapping[str, str]) -> tuple[str, ...]:
    return tuple([cells.get(column.key, "") for column in definition.columns])


def _table(name: str, entries: Iterable[Mapping[str, str]]) -> TableData:
    definition = TABLE_DEF_BY_NAME[name]
    return TableData(definition, (_ordered(definition, cells) for cells in entries))


def _component_rows(model: Model, config: ProjectConfig | None) -> Iterable[Mapping[str, str]]:
    for component in sorted(model.components.values(), key=lambda c: (c.name, c.path)):
        team = component.team
        if team is None and config is not None:
            team = config.team_of(component.name)
        yield {
            "name": component.name,
            "kind": component.kind.value,
            "package_path": component.path.rsplit("/", 1)[0],
            "source_file": component.origin.file,
            "team": team or "",
            "arxml_owner": component.owner.value,
        }


def _port_rows(model: Model, labels: Labels) -> Iterable[Mapping[str, str]]:
    components = labels.components
    ordered = sorted(
        model.ports.values(), key=lambda p: (_ref(components, p.swc_path), p.name, p.path)
    )
    for port in ordered:
        partner_count = sum(
            len(model.connectors_at(prototype.path, port.path))
            for prototype in model.prototypes_of_type(port.swc_path)
        )
        interface = model.interfaces.get(port.interface_path or "")
        yield {
            "row_id": row_id(Sheet.PORTS, port.path),
            "swc": _ref(components, port.swc_path),
            "port": port.name,
            "direction": port.direction.value,
            "interface": _ref(labels.interfaces, port.interface_path),
            "interface_kind": interface.kind.value if interface is not None else "",
            "connected": YES if partner_count else NO,
            "partner_count": str(partner_count),
            "init_value": ITEM_SEPARATOR.join(
                f"{spec.target}={spec.init_value}"
                for spec in port.com_specs
                if spec.init_value is not None
            ),
        }


def _runnable_name(model: Model, runnable_path: str | None) -> str:
    runnable = model.runnables.get(runnable_path or "")
    return runnable.name if runnable is not None else _last(runnable_path)


def _port_name(model: Model, port_path: str | None) -> str:
    port = model.ports.get(port_path or "")
    return port.name if port is not None else _last(port_path)


def _access_rows(model: Model, labels: Labels) -> Iterable[Mapping[str, str]]:
    def entries() -> Iterable[tuple[tuple[str, ...], Mapping[str, str]]]:
        for access in model.access_points.values():
            cells = {
                "row_id": row_id(Sheet.RUNNABLE_ACCESS, access.path),
                "swc": _ref(labels.components, access.swc_path),
                "runnable": _runnable_name(model, access.runnable_path),
                "port": _port_name(model, access.port_path),
                "target": access.target or "",
                "access_kind": access.kind.value,
            }
            yield (cells["swc"], cells["runnable"], cells["port"], cells["target"]), cells

    return (cells for _, cells in sorted(entries(), key=lambda entry: entry[0]))


def _trigger_rows(model: Model, labels: Labels) -> Iterable[Mapping[str, str]]:
    def entries() -> Iterable[tuple[tuple[str, ...], Mapping[str, str]]]:
        for trigger in model.triggers.values():
            mapping = model.task_of(trigger.path)
            cells = {
                "row_id": row_id(Sheet.RUNNABLE_TRIGGERS, trigger.path),
                "swc": _ref(labels.components, trigger.swc_path),
                "runnable": _runnable_name(model, trigger.runnable_path),
                "trigger_kind": trigger.kind.value,
                "port": _port_name(model, trigger.port_path),
                "target": trigger.target or "",
                "period_ms": _period_ms(trigger.period_s),
                "task": mapping.task if mapping is not None else "",
            }
            yield (cells["swc"], cells["runnable"], trigger.name), cells

    return (cells for _, cells in sorted(entries(), key=lambda entry: entry[0]))


def _prototype_name(model: Model, prototype_path: str | None) -> str:
    prototype = model.prototypes.get(prototype_path or "")
    return prototype.name if prototype is not None else _last(prototype_path)


def _composition_label(labels: Labels, composition_path: str) -> str:
    label = labels.compositions.get(composition_path)
    if label is not None:
        return label
    return labels.components.get(composition_path, composition_path)


def _connection_rows(model: Model, labels: Labels) -> Iterable[Mapping[str, str]]:
    ordered = sorted(
        model.connectors.values(),
        key=lambda c: (_last(c.composition_path), c.composition_path, c.name, c.path),
    )
    for connector in ordered:
        yield {
            "row_id": row_id(Sheet.CONNECTIONS, connector.path),
            "provider_swc": _prototype_name(model, connector.provider.prototype_path),
            "provider_port": _port_name(model, connector.provider.port_path),
            "requester_swc": _prototype_name(model, connector.requester.prototype_path),
            "requester_port": _port_name(model, connector.requester.port_path),
            "composition": _composition_label(labels, connector.composition_path),
            "connector_name": connector.name,
        }


def _instance_key(instance: PortInstance) -> tuple[str, str, str, str]:
    return (
        instance.prototype.name,
        instance.port.name,
        instance.prototype.path,
        instance.port.path,
    )


def _unconnected_rows(
    model: Model, labels: Labels, partner_limit: int
) -> Iterable[Mapping[str, str]]:
    """Unconnected port instances with the ports they could be connected to.

    A compatible partner sits in the same composition, uses the same interface and has
    the opposite direction (PR matches every direction).
    """
    by_slot: dict[tuple[str, str, PortDirection], list[PortInstance]] = defaultdict(list)
    for instance in model.port_instances():
        interface_path = instance.port.interface_path
        if interface_path is not None:
            slot = (instance.prototype.composition_path, interface_path, instance.port.direction)
            by_slot[slot].append(instance)
    for instances in by_slot.values():
        instances.sort(key=_instance_key)

    def partners(instance: PortInstance) -> str:
        interface_path = instance.port.interface_path
        if interface_path is None:
            return ""
        names: list[str] = []
        total = 0
        for direction in _OPPOSITE[instance.port.direction]:
            slot = (instance.prototype.composition_path, interface_path, direction)
            for other in by_slot.get(slot, ()):
                if other.key == instance.key:
                    continue
                total += 1
                if len(names) < partner_limit:
                    names.append(f"{other.prototype.name}.{other.port.name}")
        text = PARTNER_SEPARATOR.join(names)
        if total > len(names):
            more = f"(+{total - len(names)} more)"
            text = f"{text} {more}" if text else more
        return text

    for instance in sorted(model.unconnected(), key=_instance_key):
        key = unconnected_key(instance.prototype.path, instance.port.path)
        yield {
            "row_id": row_id(Sheet.UNCONNECTED, key),
            "swc": instance.prototype.name,
            "port": instance.port.name,
            "direction": instance.port.direction.value,
            "interface": _ref(labels.interfaces, instance.port.interface_path),
            "compatible_partners": partners(instance),
        }


def _interface_rows(model: Model, labels: Labels) -> Iterable[Mapping[str, str]]:
    for interface in sorted(model.interfaces.values(), key=lambda i: (i.name, i.path)):
        yield {
            "row_id": row_id(Sheet.INTERFACES, interface.path),
            "name": _ref(labels.interfaces, interface.path),
            "kind": interface.kind.value,
        }


def _interface_element_rows(model: Model, labels: Labels) -> Iterable[Mapping[str, str]]:
    for interface in sorted(model.interfaces.values(), key=lambda i: (i.name, i.path)):
        label = _ref(labels.interfaces, interface.path)
        for element in interface.elements:
            key = interface_element_key(interface.path, element.name)
            yield {
                "row_id": row_id(Sheet.INTERFACE_ELEMENTS, key),
                "interface": label,
                "element": element.name,
                "data_type": _ref(labels.data_types, element.type_path),
                "queued": YES if element.is_queued else NO,
            }
        for operation in interface.operations:
            if not operation.arguments:
                key = interface_element_key(interface.path, operation.name)
                yield {
                    "row_id": row_id(Sheet.INTERFACE_ELEMENTS, key),
                    "interface": label,
                    "element": operation.name,
                }
            for argument in operation.arguments:
                key = interface_element_key(interface.path, operation.name, argument.name)
                yield {
                    "row_id": row_id(Sheet.INTERFACE_ELEMENTS, key),
                    "interface": label,
                    "element": operation.name,
                    "argument": argument.name,
                    "direction": argument.direction.value,
                    "data_type": _ref(labels.data_types, argument.type_path),
                }


def _data_type_rows(model: Model, labels: Labels) -> Iterable[Mapping[str, str]]:
    for data_type in sorted(model.data_types.values(), key=lambda d: (d.name, d.path)):
        compu = model.compu_methods.get(data_type.compu_method_path or "")
        constraint = model.constraints.get(data_type.constraint_path or "")
        unit_path = data_type.unit_path or (compu.unit_path if compu is not None else None)
        is_array = data_type.category is DataTypeCategory.ARRAY
        is_record = data_type.category is DataTypeCategory.RECORD
        element_types = data_type.element_type_paths
        yield {
            "row_id": row_id(Sheet.DATA_TYPES, data_type.path),
            "name": _ref(labels.data_types, data_type.path),
            "level": data_type.level.value,
            "category": data_type.category.value,
            "base_type": _ref(labels.base_types, data_type.base_type_path),
            "lower_limit": _number(constraint.lower) if constraint is not None else "",
            "upper_limit": _number(constraint.upper) if constraint is not None else "",
            "unit": _ref(labels.units, unit_path),
            "factor": _number(compu.factor) if compu is not None else "",
            "offset": _number(compu.offset) if compu is not None else "",
            "enum_values": ITEM_SEPARATOR.join(
                f"{value}={name}" for value, name in (compu.enum_values if compu else ())
            ),
            "element_type": (
                _ref(labels.data_types, element_types[0]) if is_array and element_types else ""
            ),
            "array_size": _number(data_type.array_size),
            "record_elements": (
                ITEM_SEPARATOR.join(
                    f"{name}:{_ref(labels.data_types, path)}"
                    for name, path in zip(data_type.element_names, element_types, strict=False)
                )
                if is_record
                else ""
            ),
        }


def build_pending_table(queue: PendingQueue | None) -> TableData:
    """The pending queue as a table (requests waiting for a model owner or on hold)."""
    entries: list[Mapping[str, str]] = []
    for entry in queue.entries if queue is not None else ():
        data = entry.operation.model_dump()
        container = (
            data.get("swc_path") or data.get("composition_path") or data.get("package_path") or ""
        )
        name = data.get("name") or ".".join(
            str(data[key]) for key in ("runnable_name", "port_name", "target") if data.get(key)
        )
        entries.append(
            {
                "state": entry.state.value,
                "operation": str(data.get("op", "")),
                "object": f"{container}/{name}" if name else str(container),
                "requester": str(data.get("requester", "")),
                "created_utc": entry.created_utc,
                "owner_team": entry.owner_team or "",
                "depends_on": PARTNER_SEPARATOR.join(entry.depends_on),
                "note": entry.note,
            }
        )
    return _table("pending", entries)


def build_tables(
    model: Model, config: ProjectConfig | None, partner_limit: int = 10
) -> dict[str, TableData]:
    """Every table except ``pending``. Loops over the whole model: call it off the handlers."""
    labels = build_labels(model)
    return {
        "components": _table("components", _component_rows(model, config)),
        "ports": _table("ports", _port_rows(model, labels)),
        "runnable_access": _table("runnable_access", _access_rows(model, labels)),
        "triggers": _table("triggers", _trigger_rows(model, labels)),
        "connections": _table("connections", _connection_rows(model, labels)),
        "unconnected": _table("unconnected", _unconnected_rows(model, labels, partner_limit)),
        "interfaces": _table("interfaces", _interface_rows(model, labels)),
        "interface_elements": _table("interface_elements", _interface_element_rows(model, labels)),
        "data_types": _table("data_types", _data_type_rows(model, labels)),
    }


# --------------------------------------------------------------------------- dropdown lists


def _sorted_unique(values: Iterable[str]) -> list[str]:
    return sorted(set(values), key=lambda text: (text.lower(), text))


def build_lists(model: Model) -> dict[str, list[str]]:
    """Values of the request form dropdowns, under the list names of ``porttool.core.sheets``."""
    labels = build_labels(model)
    return {
        "actions": [member.value for member in Action],
        "yes_no": [YES, NO],
        "data_type_levels": [member.value for member in DataTypeLevel],
        "data_type_categories": [member.value for member in DataTypeCategory],
        "interface_kinds": [member.value for member in InterfaceKind],
        "argument_directions": [member.value for member in ArgumentDirection],
        "port_directions": [member.value for member in PortDirection],
        "access_kinds": [member.value for member in AccessKind],
        "trigger_kinds": [member.value for member in TriggerKind],
        "swcs": _sorted_unique(labels.components.values()),
        "prototypes": _sorted_unique(p.name for p in model.prototypes.values()),
        "interfaces": _sorted_unique(labels.interfaces.values()),
        "data_types": _sorted_unique(labels.data_types.values()),
        "base_types": _sorted_unique(labels.base_types.values()),
        "units": _sorted_unique(labels.units.values()),
        "tasks": _sorted_unique(model.tasks),
        "compositions": _sorted_unique(labels.compositions.values()),
    }


def swc_details(model: Model, swc_path: str) -> dict[str, list[str]]:
    """Port and runnable names of one SWC type, for the suggestions of the request form."""
    return {
        "ports": _sorted_unique(port.name for port in model.ports_of(swc_path)),
        "runnables": _sorted_unique(r.name for r in model.runnables_of(swc_path)),
    }


def prototype_ports(model: Model, prototype_name: str) -> list[str]:
    """Port names of every prototype with this short name."""
    names: list[str] = []
    for prototype in model.prototypes.values():
        if prototype.name == prototype_name and prototype.type_path is not None:
            names.extend(port.name for port in model.ports_of(prototype.type_path))
    return _sorted_unique(names)
