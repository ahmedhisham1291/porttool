"""The request form of the page: its fields, and request rows built from what it sends.

The fields are the editable columns of the request sheets in ``porttool.core.sheets``,
so a request typed into the page is the same ``RawRequestRow`` a workbook row becomes.
"""

from __future__ import annotations

from collections.abc import Mapping

from porttool.core.results import RawRequestRow, RowRef
from porttool.core.sheets import COLUMNS, REQUEST_SHEETS, Action, Column, Sheet
from porttool.gui.errors import ApiError

#: File name shown as the source of a request typed into the page.
FORM_SOURCE = "Request form"
_MAX_CELL_CHARS = 4000

#: Lists with a fixed set of values: the page shows them as a closed dropdown.
_CLOSED_LISTS = frozenset(
    {
        "actions",
        "yes_no",
        "data_type_levels",
        "data_type_categories",
        "interface_kinds",
        "argument_directions",
        "port_directions",
        "access_kinds",
        "trigger_kinds",
    }
)
#: Columns the tool computes: never part of a request.
_COMPUTED = frozenset({"row_id", "action", "interface_kind", "connected", "partner_count"})
_COMMON = frozenset({"requester", "comment"})

_REQUIRED: Mapping[Sheet, frozenset[str]] = {
    Sheet.PORTS: frozenset({"swc", "port", "direction", "interface"}),
    Sheet.RUNNABLE_ACCESS: frozenset({"swc", "runnable", "port", "target", "access_kind"}),
    Sheet.RUNNABLE_TRIGGERS: frozenset({"swc", "runnable", "trigger_kind"}),
    Sheet.CONNECTIONS: frozenset(
        {"provider_swc", "provider_port", "requester_swc", "requester_port"}
    ),
    Sheet.INTERFACES: frozenset({"name", "kind"}),
    Sheet.INTERFACE_ELEMENTS: frozenset({"element"}),
    Sheet.DATA_TYPES: frozenset({"name", "level", "category"}),
}

#: (sheet, field) -> (field that names the owner, kind of owner, list of the owner's names).
_SUGGEST: Mapping[tuple[Sheet, str], tuple[str, str, str]] = {
    (Sheet.RUNNABLE_ACCESS, "runnable"): ("swc", "swc", "runnables"),
    (Sheet.RUNNABLE_ACCESS, "port"): ("swc", "swc", "ports"),
    (Sheet.RUNNABLE_TRIGGERS, "runnable"): ("swc", "swc", "runnables"),
    (Sheet.RUNNABLE_TRIGGERS, "port"): ("swc", "swc", "ports"),
    (Sheet.CONNECTIONS, "provider_port"): ("provider_swc", "prototype", "ports"),
    (Sheet.CONNECTIONS, "requester_port"): ("requester_swc", "prototype", "ports"),
}

_HINTS: Mapping[str, str] = {
    "enum_values": "For example 0=OFF;1=ON",
    "record_elements": "For example Speed:uint16;Valid:boolean",
    "init_value": "For example 0, or Speed=0;Valid=1",
    "connector_name": "Leave empty to use the naming rule",
    "period_ms": "Only for a periodic trigger",
    "task": "Leave empty to leave the event unmapped",
}


def _field(sheet: Sheet, column: Column) -> dict[str, object]:
    field: dict[str, object] = {
        "key": column.key,
        "label": column.header,
        "required": column.key in _REQUIRED.get(sheet, frozenset()),
    }
    if column.dropdown is not None:
        field["list"] = column.dropdown
        field["closed"] = column.dropdown in _CLOSED_LISTS
    suggest = _SUGGEST.get((sheet, column.key))
    if suggest is not None:
        field["suggest"] = {"owner_field": suggest[0], "owner": suggest[1], "names": suggest[2]}
    hint = _HINTS.get(column.key)
    if hint is not None:
        field["hint"] = hint
    return field


def _fields(sheet: Sheet, skip: frozenset[str] = frozenset()) -> list[dict[str, object]]:
    return [
        _field(sheet, column)
        for column in COLUMNS[sheet]
        if column.key not in _COMPUTED and column.key not in _COMMON and column.key not in skip
    ]


def form_schema() -> dict[str, object]:
    """What the page needs to draw the request form. Does not depend on the model."""

    def kind(name: str, title: str, sheet: Sheet) -> dict[str, object]:
        return {"kind": name, "title": title, "sheet": sheet.value, "fields": _fields(sheet)}

    interface = kind("interface", "Interface with elements", Sheet.INTERFACES)
    interface["children"] = {
        "sheet": Sheet.INTERFACE_ELEMENTS.value,
        "title": "Elements, operations and arguments",
        "link_field": "interface",  # filled from the parent's ...
        "link_from": "name",
        "fields": _fields(Sheet.INTERFACE_ELEMENTS, skip=frozenset({"interface"})),
    }
    return {
        "action": Action.ADD.value,
        "common": [
            {"key": "requester", "label": "Requester", "required": False},
            {"key": "comment", "label": "Comment", "required": False},
        ],
        "kinds": [
            kind("port", "Port", Sheet.PORTS),
            kind("runnable_access", "Runnable access", Sheet.RUNNABLE_ACCESS),
            kind("trigger", "Trigger", Sheet.RUNNABLE_TRIGGERS),
            kind("connection", "Connection", Sheet.CONNECTIONS),
            interface,
            kind("data_type", "Data type", Sheet.DATA_TYPES),
        ],
    }


def _bad(message: str) -> ApiError:
    return ApiError(400, "bad-request", message)


def rows_from_json(payload: object, max_rows: int) -> list[RawRequestRow]:
    """Build ``RawRequestRow`` objects from the body of ``POST /api/dry-run/rows``.

    Expected: ``{"rows": [{"sheet": "Ports", "values": {"swc": "SwcA", ...},
    "requester": "...", "comment": "...", "action": "ADD"}, ...]}``. ``action`` defaults
    to ADD. Every problem is a 400 with a message that names the row.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
        raise _bad("The request must be a JSON object with a list 'rows'.")
    raw_rows: list[object] = payload["rows"]
    if not raw_rows:
        raise _bad("There is no request to validate. Add at least one request to the list.")
    if len(raw_rows) > max_rows:
        raise _bad(f"Too many requests in one validation ({len(raw_rows)}, limit {max_rows}).")
    sheets = {sheet.value: sheet for sheet in REQUEST_SHEETS}
    actions = {member.value for member in Action}
    rows: list[RawRequestRow] = []
    for number, raw in enumerate(raw_rows, start=1):
        if not isinstance(raw, dict):
            raise _bad(f"Request {number} is not an object.")
        sheet = sheets.get(str(raw.get("sheet", "")))
        if sheet is None:
            raise _bad(f"Request {number}: unknown sheet '{str(raw.get('sheet', ''))[:60]}'.")
        given = raw.get("values", {})
        if not isinstance(given, dict):
            raise _bad(f"Request {number}: 'values' must be an object.")
        keys = [column.key for column in COLUMNS[sheet]]
        unknown = sorted(str(key) for key in given if key not in keys)
        if unknown:
            raise _bad(f"Request {number}: sheet {sheet.value} has no column '{unknown[0][:60]}'.")
        values = {key: "" for key in keys}
        for key, value in given.items():
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, str | int | float):
                raise _bad(f"Request {number}: the value of '{key}' must be text or a number.")
            text = str(value).strip()
            if len(text) > _MAX_CELL_CHARS:
                raise _bad(f"Request {number}: the value of '{key}' is too long.")
            values[str(key)] = text
        action = str(raw.get("action") or Action.ADD.value).strip().upper()
        if action not in actions:
            raise _bad(f"Request {number}: unknown action '{action[:20]}'.")
        requester = str(raw.get("requester") or values.get("requester") or "").strip()
        comment = str(raw.get("comment") or values.get("comment") or "").strip()
        values["action"] = action
        values["requester"] = requester[:_MAX_CELL_CHARS]
        values["comment"] = comment[:_MAX_CELL_CHARS]
        rows.append(
            RawRequestRow(
                sheet=sheet.value,
                row=RowRef(file=FORM_SOURCE, sheet=sheet.value, row=number),
                row_id=values.get("row_id") or None,
                action=action,
                requester=values["requester"],
                comment=values["comment"],
                values=values,
            )
        )
    return rows
