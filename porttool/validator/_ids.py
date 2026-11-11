"""Deterministic operation ids: ``<sheet>:<operation kind>:<names>``. Internal.

The same request always gets the same id, in every import. The pending queue relies on
that to recognise a request it already holds.
"""

from __future__ import annotations

from enum import StrEnum

from porttool.core.sheets import Sheet


def slug(kind: StrEnum) -> str:
    return kind.name.lower()


def _make(sheet: Sheet, op: str, *parts: str) -> str:
    return f"{sheet.value}:{op}:{'/'.join(parts)}"


def add_data_type(path: str) -> str:
    return _make(Sheet.DATA_TYPES, "add_data_type", path)


def add_interface(path: str) -> str:
    return _make(Sheet.INTERFACES, "add_interface", path)


def add_port(swc_path: str, name: str) -> str:
    return _make(Sheet.PORTS, "add_port", swc_path, name)


def modify_port(port_path: str) -> str:
    return _make(Sheet.PORTS, "modify_port", port_path)


def delete_port(port_path: str) -> str:
    return _make(Sheet.PORTS, "delete_port", port_path)


def add_server_runnable(sheet: Sheet, swc_path: str, name: str) -> str:
    return _make(sheet, "add_server_runnable", swc_path, name)


def add_trigger(
    sheet: Sheet,
    swc_path: str,
    runnable: str,
    kind: StrEnum,
    port: str | None,
    target: str | None,
    period_s: float | None,
) -> str:
    parts = [swc_path, runnable, slug(kind)]
    if port:
        parts.append(port)
    if target:
        parts.append(target)
    if period_s is not None:
        parts.append(f"{period_s * 1000.0:g}ms")
    return _make(sheet, "add_trigger", *parts)


def delete_trigger(swc_path: str, name: str) -> str:
    return _make(Sheet.RUNNABLE_TRIGGERS, "delete_trigger", swc_path, name)


def access_point(
    op: str, swc_path: str, runnable: str, port: str, target: str, kind: StrEnum
) -> str:
    return _make(Sheet.RUNNABLE_ACCESS, op, swc_path, runnable, port, target, slug(kind))


def add_connector(
    composition_path: str,
    provider_prototype: str,
    provider_port: str,
    requester_prototype: str,
    requester_port: str,
) -> str:
    return _make(
        Sheet.CONNECTIONS,
        "add_connector",
        composition_path,
        f"{provider_prototype}.{provider_port}",
        f"{requester_prototype}.{requester_port}",
    )


def delete_connector(connector_path: str) -> str:
    return _make(Sheet.CONNECTIONS, "delete_connector", connector_path)
