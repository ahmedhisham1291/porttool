"""Text of the README sheet: the edit rules in plain language, a legend, the baseline."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from porttool.core.sheets import COLUMNS, REQUEST_SHEETS, Action, Sheet, WorkbookMeta
from porttool.workbook.layout import KEY_ACTION, KEY_COMMENT, KEY_REQUESTER, KEY_ROW_ID


class Style(Enum):
    TITLE = "title"
    HEADING = "heading"
    TEXT = "text"
    LEGEND_EDITABLE = "legend-editable"  # column A shows the colour of an editable cell
    LEGEND_LOCKED = "legend-locked"  # column A shows the colour of a locked cell
    LEGEND_HEADER = "legend-header"
    LEGEND_REQUEST_HEADER = "legend-request-header"


@dataclass(frozen=True, slots=True)
class ReadmeLine:
    style: Style
    left: str = ""
    right: str = ""


_SHEET_PURPOSE: dict[Sheet, str] = {
    Sheet.COMPONENTS: "Read only. Every software component with its owner team and who "
    "maintains its ARXML.",
    Sheet.DATA_TYPES: "Data types. Add a row to request a new data type.",
    Sheet.INTERFACES: "Port interfaces. Add a row to request a new interface.",
    Sheet.INTERFACE_ELEMENTS: "Data elements of an interface, and the arguments of "
    "client-server operations (one row per argument).",
    Sheet.PORTS: "Ports of each component type. Add a row to request a new port.",
    Sheet.RUNNABLE_ACCESS: "Which runnable reads or writes which port element, and how "
    "(implicit, explicit, server call).",
    Sheet.RUNNABLE_TRIGGERS: "What starts each runnable, and the task it runs in.",
    Sheet.CONNECTIONS: "Connectors between component prototypes. Add a row to connect two ports.",
    Sheet.UNCONNECTED: "Read only. Ports without a connector, with the ports they could be "
    "connected to.",
    Sheet.RESULTS: "Read only. What PortTool did with the requests of the last import.",
}


def _header(sheet: Sheet, key: str) -> str:
    return next(column.header for column in COLUMNS[sheet] if column.key == key)


def readme_lines(meta: WorkbookMeta, blank_rows: int) -> tuple[ReadmeLine, ...]:
    """Return the README sheet, top to bottom. Header texts come from the layout contract."""
    sample = REQUEST_SHEETS[0]
    action = _header(sample, KEY_ACTION)
    requester = _header(sample, KEY_REQUESTER)
    comment = _header(sample, KEY_COMMENT)
    row_id = _header(sample, KEY_ROW_ID)
    actions = ", ".join(member.value for member in Action)
    text = Style.TEXT
    lines = [
        ReadmeLine(Style.TITLE, "PortTool request workbook"),
        ReadmeLine(
            text,
            "",
            "This workbook is a request form. It shows what exists in the ECU project and "
            "lets you ask for changes. Nothing changes in the project until PortTool has "
            "imported this file and every request has passed validation.",
        ),
        ReadmeLine(Style.HEADING, "Baseline"),
        ReadmeLine(text, "Baseline commit", meta.baseline_commit or "(not known)"),
        ReadmeLine(text, "Exported (UTC)", meta.export_time_utc),
        ReadmeLine(text, "Model hash", meta.model_hash),
        ReadmeLine(text, "AUTOSAR schema", meta.autosar_schema or "(not known)"),
        ReadmeLine(text, "Tool version", meta.tool_version),
        ReadmeLine(text, "Layout version", str(meta.layout_version)),
        ReadmeLine(
            text,
            "",
            "Requests are checked against the project as it is when the file comes back. "
            "If the project has moved on since this export, PortTool says so and checks "
            "every request again.",
        ),
        ReadmeLine(Style.HEADING, "How to enter a request"),
        ReadmeLine(
            text,
            "Change or remove",
            f"Find the row of the existing object. Fill the coloured cells at the end of "
            f"that row: {action} ({actions}), {requester}, {comment}. The other cells of "
            f"an existing row are locked.",
        ),
        ReadmeLine(
            text,
            "Add something new",
            f"Go to the coloured blank rows below the data ({blank_rows} per sheet). "
            f"Leave {row_id} empty, fill the columns, set {action} to {Action.ADD.value}. "
            f"A row without {action} is not a request and is reported back to you.",
        ),
        ReadmeLine(
            text,
            "Dropdown lists",
            "Pick from the list where a cell offers one. If a name is not in the list, "
            "you may still type it when another row of this workbook creates it (for "
            f"example a new interface on the {Sheet.INTERFACES.value} sheet used by a new "
            "port).",
        ),
        ReadmeLine(
            text,
            "Names",
            "Type names exactly as the project writes them: upper and lower case matter. "
            "A name that repeats in the project is shown with its full path, starting "
            "with a slash.",
        ),
        ReadmeLine(Style.HEADING, "Rules"),
        ReadmeLine(
            text, "1", f"Do not change or type a {row_id}. PortTool uses it to find the row."
        ),
        ReadmeLine(text, "2", "Do not rename, move, add or delete sheets or columns."),
        ReadmeLine(text, "3", "Do not merge cells and do not use formulas. Type plain values."),
        ReadmeLine(
            text,
            "4",
            "Filtering and hiding rows is fine: hidden and filtered rows are still imported.",
        ),
        ReadmeLine(
            text,
            "5",
            "Save as Excel Workbook (.xlsx). PortTool does not read .xls, .xlsm or .csv, "
            "and it does not read a password-protected file.",
        ),
        ReadmeLine(text, "6", "Close the file in Excel before you hand it back to PortTool."),
        ReadmeLine(Style.HEADING, "Cell formats"),
        ReadmeLine(
            text,
            _header(Sheet.DATA_TYPES, "enum_values"),
            "value=NAME pairs separated by a semicolon: 0=OFF;1=ON",
        ),
        ReadmeLine(
            text,
            _header(Sheet.DATA_TYPES, "record_elements"),
            "name:Type pairs separated by a semicolon: speed:uint16;valid:boolean",
        ),
        ReadmeLine(
            text,
            _header(Sheet.PORTS, "init_value"),
            "element=value pairs separated by a semicolon: Speed=0",
        ),
        ReadmeLine(text, "Yes / no", "Y or N"),
        ReadmeLine(
            text,
            f"{_header(Sheet.CONNECTIONS, 'provider_swc')} / "
            f"{_header(Sheet.CONNECTIONS, 'requester_swc')}",
            f"On the {Sheet.CONNECTIONS.value} sheet these are the names of the component "
            "prototypes inside the composition. A cell is empty where the connector ends "
            "at the composition's own port.",
        ),
        ReadmeLine(Style.HEADING, "Legend"),
        ReadmeLine(Style.LEGEND_EDITABLE, "", "You can type here: request cells and blank rows."),
        ReadmeLine(Style.LEGEND_LOCKED, "", "Locked: the current state of the project."),
        ReadmeLine(Style.LEGEND_HEADER, "", "Column that describes the object."),
        ReadmeLine(Style.LEGEND_REQUEST_HEADER, "", "Column you fill to make a request."),
        ReadmeLine(Style.HEADING, "Sheets"),
    ]
    lines.extend(
        ReadmeLine(text, sheet.value, purpose) for sheet, purpose in _SHEET_PURPOSE.items()
    )
    return tuple(lines)
