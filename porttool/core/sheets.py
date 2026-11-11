"""Workbook layout: sheet names, column keys, header texts. CONTRACT FILE.

The workbook package writes and reads these columns. The validator reads
``RawRequestRow.values`` by the column keys defined here. Change nothing without
changing both sides.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class Sheet(StrEnum):
    README = "README"
    COMPONENTS = "Components"
    DATA_TYPES = "DataTypes"
    INTERFACES = "Interfaces"
    INTERFACE_ELEMENTS = "InterfaceElements"
    PORTS = "Ports"
    RUNNABLE_ACCESS = "RunnableAccess"
    RUNNABLE_TRIGGERS = "RunnableTriggers"
    CONNECTIONS = "Connections"
    UNCONNECTED = "Unconnected"
    LISTS = "Lists"  # hidden: dropdown sources
    META = "Meta"  # hidden: baseline information
    RESULTS = "Results"


class Action(StrEnum):
    ADD = "ADD"
    MODIFY = "MODIFY"
    DELETE = "DELETE"


@dataclass(frozen=True, slots=True)
class Column:
    key: str  # stable key used in RawRequestRow.values
    header: str  # text shown in row 1 of the sheet
    editable: bool = False  # True: unlocked on existing rows too (Action, Requester, Comment)
    dropdown: str | None = None  # name of a list on the Lists sheet, if any


_REQUEST = (
    Column("action", "Action", editable=True, dropdown="actions"),
    Column("requester", "Requester", editable=True),
    Column("comment", "Comment", editable=True),
)

_COLUMNS: dict[Sheet, tuple[Column, ...]] = {
    Sheet.COMPONENTS: (
        Column("name", "SWC"),
        Column("kind", "Kind"),
        Column("package_path", "Package path"),
        Column("source_file", "Source file"),
        Column("team", "Owner team"),
        Column("arxml_owner", "ARXML owner"),
    ),
    Sheet.DATA_TYPES: (
        Column("row_id", "RowID"),
        Column("name", "Data type"),
        Column("level", "Level", dropdown="data_type_levels"),
        Column("category", "Category", dropdown="data_type_categories"),
        Column("base_type", "Base type", dropdown="base_types"),
        Column("lower_limit", "Lower limit"),
        Column("upper_limit", "Upper limit"),
        Column("unit", "Unit", dropdown="units"),
        Column("factor", "Factor"),
        Column("offset", "Offset"),
        Column("enum_values", "Enumeration values"),  # "0=OFF;1=ON"
        Column("element_type", "Array element type", dropdown="data_types"),
        Column("array_size", "Array size"),
        Column("record_elements", "Record elements"),  # "name:Type;name2:Type2"
        *_REQUEST,
    ),
    Sheet.INTERFACES: (
        Column("row_id", "RowID"),
        Column("name", "Interface"),
        Column("kind", "Kind", dropdown="interface_kinds"),
        *_REQUEST,
    ),
    Sheet.INTERFACE_ELEMENTS: (
        Column("row_id", "RowID"),
        Column("interface", "Interface", dropdown="interfaces"),
        Column("element", "Element or operation"),
        Column("argument", "Argument"),
        Column("direction", "Argument direction", dropdown="argument_directions"),
        Column("data_type", "Data type", dropdown="data_types"),
        Column("queued", "Queued (Y/N)", dropdown="yes_no"),
        *_REQUEST,
    ),
    Sheet.PORTS: (
        Column("row_id", "RowID"),
        Column("swc", "SWC", dropdown="swcs"),
        Column("port", "Port"),
        Column("direction", "Direction", dropdown="port_directions"),
        Column("interface", "Interface", dropdown="interfaces"),
        Column("interface_kind", "Interface kind"),
        Column("connected", "Connected (Y/N)"),
        Column("partner_count", "Partners"),
        Column("init_value", "Init value"),
        *_REQUEST,
    ),
    Sheet.RUNNABLE_ACCESS: (
        Column("row_id", "RowID"),
        Column("swc", "SWC", dropdown="swcs"),
        Column("runnable", "Runnable"),
        Column("port", "Port"),
        Column("target", "Element or operation"),
        Column("access_kind", "Access kind", dropdown="access_kinds"),
        *_REQUEST,
    ),
    Sheet.RUNNABLE_TRIGGERS: (
        Column("row_id", "RowID"),
        Column("swc", "SWC", dropdown="swcs"),
        Column("runnable", "Runnable"),
        Column("trigger_kind", "Trigger kind", dropdown="trigger_kinds"),
        Column("port", "Port"),
        Column("target", "Element, operation or mode"),
        Column("period_ms", "Period (ms)"),
        Column("task", "Task", dropdown="tasks"),
        *_REQUEST,
    ),
    Sheet.CONNECTIONS: (
        Column("row_id", "RowID"),
        Column("provider_swc", "Provider SWC", dropdown="prototypes"),
        Column("provider_port", "Provider port"),
        Column("requester_swc", "Requester SWC", dropdown="prototypes"),
        Column("requester_port", "Requester port"),
        Column("composition", "Composition", dropdown="compositions"),
        Column("connector_name", "Connector name"),
        *_REQUEST,
    ),
    Sheet.UNCONNECTED: (
        Column("row_id", "Port RowID"),
        Column("swc", "SWC"),
        Column("port", "Port"),
        Column("direction", "Direction"),
        Column("interface", "Interface"),
        Column("compatible_partners", "Compatible partner ports"),
    ),
    Sheet.RESULTS: (
        Column("row_id", "RowID"),
        Column("sheet", "Sheet"),
        Column("status", "Status"),
        Column("code", "Code"),
        Column("reason", "Reason"),
    ),
}

COLUMNS: Mapping[Sheet, tuple[Column, ...]] = MappingProxyType(_COLUMNS)

#: Sheets whose rows can carry a request (an Action).
REQUEST_SHEETS: tuple[Sheet, ...] = (
    Sheet.DATA_TYPES,
    Sheet.INTERFACES,
    Sheet.INTERFACE_ELEMENTS,
    Sheet.PORTS,
    Sheet.RUNNABLE_ACCESS,
    Sheet.RUNNABLE_TRIGGERS,
    Sheet.CONNECTIONS,
)


@dataclass(frozen=True, slots=True)
class WorkbookMeta:
    """Content of the hidden Meta sheet."""

    tool_version: str
    layout_version: int
    autosar_schema: str | None
    baseline_commit: str | None
    model_hash: str
    export_time_utc: str


LAYOUT_VERSION = 1
