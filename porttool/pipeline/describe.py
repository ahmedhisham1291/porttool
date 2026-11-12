"""Short texts for operations (commit message, report, request sheets)."""

from __future__ import annotations

from porttool.core.operations import (
    AddAccessPoint,
    AddConnector,
    AddDataType,
    AddInterface,
    AddPort,
    AddServerRunnable,
    AddTrigger,
    AnyOperation,
    DeleteAccessPoint,
    DeleteConnector,
    DeletePort,
    DeleteTrigger,
    ModifyPort,
)


def owner_path(op: AnyOperation) -> str:
    """The SWC, composition or package the operation works in."""
    if isinstance(op, AddDataType | AddInterface):
        return op.package_path
    if isinstance(op, AddConnector | DeleteConnector):
        return op.composition_path
    return op.swc_path


def object_text(op: AnyOperation) -> str:
    """The object the operation creates, changes or removes."""
    if isinstance(op, AddDataType | AddInterface):
        return f"{op.package_path}/{op.name}"
    if isinstance(op, AddPort):
        return f"{op.swc_path}/{op.name} ({op.direction.value}, {op.interface_path})"
    if isinstance(op, ModifyPort | DeletePort):
        return f"{op.swc_path}/{op.name}"
    if isinstance(op, AddServerRunnable):
        return f"{op.swc_path}/{op.name} (serves {op.port_name}.{op.operation})"
    if isinstance(op, AddAccessPoint | DeleteAccessPoint):
        return f"{op.swc_path}/{op.runnable_name}: {op.kind.value} {op.port_name}.{op.target}"
    if isinstance(op, AddTrigger):
        what = op.kind.value
        if op.port_name:
            what += f" {op.port_name}.{op.target or ''}"
        if op.period_s is not None:
            what += f" {op.period_s * 1000:g} ms"
        task = f", task {op.task}" if op.task else ", no task"
        return f"{op.swc_path}/{op.runnable_name}: {what}{task}"
    if isinstance(op, DeleteTrigger):
        return f"{op.swc_path}/{op.name}"
    if isinstance(op, AddConnector):
        return (
            f"{op.composition_path}/{op.name} ({op.provider_prototype}.{op.provider_port} -> "
            f"{op.requester_prototype}.{op.requester_port})"
        )
    return f"{op.composition_path}/{op.name}"


def operation_text(op: AnyOperation) -> str:
    return f"{op.op.replace('_', ' ')} {object_text(op)}"


def source_text(op: AnyOperation) -> str:
    """Where the request came from: ``requests.xlsx Ports row 14``, or empty."""
    if op.source_file is None:
        return ""
    name = op.source_file.replace("\\", "/").rsplit("/", 1)[-1]
    row = f" row {op.source_row}" if op.source_row is not None else ""
    return f"{name} {op.source_sheet or ''}{row}".strip()
