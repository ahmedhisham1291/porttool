"""Typed change operations and the ChangeSet. CONTRACT FILE.

The validator produces a ChangeSet. The writer and the pipeline consume nothing else.
Everything here round-trips through JSON (``ChangeSet.to_json`` / ``ChangeSet.from_json``).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from porttool.core.models import (
    AccessKind,
    ArgumentDirection,
    DataTypeCategory,
    DataTypeLevel,
    InterfaceKind,
    PortDirection,
    TriggerKind,
)


class Route(StrEnum):
    APPLY = "apply"  # write into the workspace now
    MODEL = "model"  # model-owned SWC: goes to the model owner's request package
    HOLD = "hold"  # depends on something that does not exist yet


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class OperationBase(_Frozen):
    id: str  # unique inside one ChangeSet
    requester: str = ""
    comment: str = ""
    route: Route = Route.APPLY
    source_file: str | None = None  # workbook the request came from
    source_sheet: str | None = None
    source_row: int | None = None


# ---- specs used inside operations


class ElementSpec(_Frozen):
    name: str
    type_path: str
    is_queued: bool = False


class ArgumentSpec(_Frozen):
    name: str
    direction: ArgumentDirection
    type_path: str


class OperationSpec(_Frozen):
    name: str
    arguments: tuple[ArgumentSpec, ...] = ()


class RecordElementSpec(_Frozen):
    name: str
    type_path: str


class InitValueSpec(_Frozen):
    """Init value of one data element. ``value`` None means an explicit "none"."""

    element: str
    value: str | None


# ---- add operations


class AddDataType(OperationBase):
    op: Literal["add_data_type"] = "add_data_type"
    package_path: str
    name: str
    level: DataTypeLevel
    category: DataTypeCategory
    base_type_path: str | None = None
    lower_limit: float | None = None
    upper_limit: float | None = None
    unit: str | None = None
    factor: float | None = None
    offset: float | None = None
    enum_values: tuple[tuple[int, str], ...] = ()
    element_type_path: str | None = None  # array
    array_size: int | None = None
    record_elements: tuple[RecordElementSpec, ...] = ()


class AddInterface(OperationBase):
    op: Literal["add_interface"] = "add_interface"
    package_path: str
    name: str
    kind: InterfaceKind
    elements: tuple[ElementSpec, ...] = ()
    operations: tuple[OperationSpec, ...] = ()


class AddPort(OperationBase):
    op: Literal["add_port"] = "add_port"
    swc_path: str
    name: str
    direction: PortDirection
    interface_path: str
    init_values: tuple[InitValueSpec, ...] = ()


class AddServerRunnable(OperationBase):
    """Runnable that implements one operation of a new server port. No other runnable is created."""

    op: Literal["add_server_runnable"] = "add_server_runnable"
    swc_path: str
    name: str
    symbol: str
    port_name: str
    operation: str


class AddAccessPoint(OperationBase):
    op: Literal["add_access_point"] = "add_access_point"
    swc_path: str
    runnable_name: str
    port_name: str
    target: str  # data element or operation name
    kind: AccessKind


class AddTrigger(OperationBase):
    op: Literal["add_trigger"] = "add_trigger"
    swc_path: str
    runnable_name: str
    kind: TriggerKind
    port_name: str | None = None
    target: str | None = None
    period_s: float | None = None
    task: str | None = None  # None: leave the event unmapped
    position_after_event: str | None = None  # path of the event this one is placed after


class AddConnector(OperationBase):
    op: Literal["add_connector"] = "add_connector"
    composition_path: str
    name: str
    provider_prototype: str  # prototype short name inside the composition
    provider_port: str
    requester_prototype: str
    requester_port: str


# ---- modify / delete (applied after the pilot; defined now so the JSON format is stable)


class ModifyPort(OperationBase):
    op: Literal["modify_port"] = "modify_port"
    swc_path: str
    name: str
    interface_path: str | None = None
    init_values: tuple[InitValueSpec, ...] = ()


class DeletePort(OperationBase):
    op: Literal["delete_port"] = "delete_port"
    swc_path: str
    name: str


class DeleteConnector(OperationBase):
    op: Literal["delete_connector"] = "delete_connector"
    composition_path: str
    name: str


class DeleteAccessPoint(OperationBase):
    op: Literal["delete_access_point"] = "delete_access_point"
    swc_path: str
    runnable_name: str
    port_name: str
    target: str
    kind: AccessKind


class DeleteTrigger(OperationBase):
    op: Literal["delete_trigger"] = "delete_trigger"
    swc_path: str
    name: str


AnyOperation = Annotated[
    AddDataType
    | AddInterface
    | AddPort
    | AddServerRunnable
    | AddAccessPoint
    | AddTrigger
    | AddConnector
    | ModifyPort
    | DeletePort
    | DeleteConnector
    | DeleteAccessPoint
    | DeleteTrigger,
    Field(discriminator="op"),
]

#: Apply order of the operation kinds. Deletes run first, in reverse dependency order.
APPLY_ORDER: tuple[str, ...] = (
    "delete_connector",
    "delete_trigger",
    "delete_access_point",
    "delete_port",
    "add_data_type",
    "add_interface",
    "add_port",
    "modify_port",
    "add_server_runnable",
    "add_access_point",
    "add_trigger",
    "add_connector",
)


class ChangeSet(_Frozen):
    format_version: int = 1
    baseline_commit: str | None = None
    model_hash: str | None = None
    created_utc: str = ""
    operations: tuple[AnyOperation, ...] = ()

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)

    @classmethod
    def from_json(cls, text: str) -> ChangeSet:
        return cls.model_validate_json(text)

    def routed(self, route: Route) -> tuple[AnyOperation, ...]:
        return tuple(op for op in self.operations if op.route is route)
