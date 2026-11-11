"""Stable row identifiers shared by the workbook and the validator. CONTRACT FILE.

A RowID identifies an existing object in a workbook row. It survives sorting and
filtering in Excel. New rows (Action = ADD) have no RowID.

Key per sheet (``key`` argument of :func:`row_id`):

* DataTypes          data type path
* Interfaces         interface path
* InterfaceElements  ``<interface path>#<element or operation>#<argument or empty>``
* Ports              port path (port of the SWC type)
* RunnableAccess     access point path
* RunnableTriggers   trigger path
* Connections        connector path
* Unconnected        ``<prototype path>|<port path>``
"""

from __future__ import annotations

import hashlib

from porttool.core.sheets import Sheet

_PREFIX: dict[Sheet, str] = {
    Sheet.DATA_TYPES: "DT",
    Sheet.INTERFACES: "IF",
    Sheet.INTERFACE_ELEMENTS: "IE",
    Sheet.PORTS: "PT",
    Sheet.RUNNABLE_ACCESS: "RA",
    Sheet.RUNNABLE_TRIGGERS: "RT",
    Sheet.CONNECTIONS: "CN",
    Sheet.UNCONNECTED: "UC",
}


def row_id(sheet: Sheet, key: str) -> str:
    """Return the RowID of the object ``key`` on ``sheet``, e.g. ``PT-3f9a1c0b7d2e``."""
    digest = hashlib.sha1(key.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]
    return f"{_PREFIX[sheet]}-{digest}"


def interface_element_key(interface_path: str, element: str, argument: str = "") -> str:
    return f"{interface_path}#{element}#{argument}"


def unconnected_key(prototype_path: str, port_path: str) -> str:
    return f"{prototype_path}|{port_path}"
