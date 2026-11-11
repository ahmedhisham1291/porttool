"""Immutable model of the AUTOSAR repo. CONTRACT FILE.

Rules:
* Every object carries its absolute AUTOSAR short-name path and an ``Origin``.
* References to other objects are absolute paths (strings); resolve them through ``Model``.
* Nothing here is mutable. A reload builds a new ``Model`` and swaps the reference.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol, TypeVar

from porttool.core.results import Finding

# --------------------------------------------------------------------------- enums


class ComponentKind(StrEnum):
    APPLICATION = "application"
    SENSOR_ACTUATOR = "sensor-actuator"
    COMPLEX_DEVICE_DRIVER = "complex-device-driver"
    ECU_ABSTRACTION = "ecu-abstraction"
    SERVICE = "service"
    SERVICE_PROXY = "service-proxy"
    NV_BLOCK = "nv-block"
    PARAMETER = "parameter"
    COMPOSITION = "composition"
    OTHER = "other"


class ArxmlOwner(StrEnum):
    DAVINCI = "davinci"  # PortTool may edit it
    MODEL = "model"  # regenerated from Simulink / TargetLink: route requests to its owner
    LOCKED = "locked"  # never edited: supplier, service or BSW-generated component


class PortDirection(StrEnum):
    P = "P"
    R = "R"
    PR = "PR"


class InterfaceKind(StrEnum):
    SENDER_RECEIVER = "sender-receiver"
    CLIENT_SERVER = "client-server"
    MODE_SWITCH = "mode-switch"
    PARAMETER = "parameter"
    NV_DATA = "nv-data"
    TRIGGER = "trigger"


class ArgumentDirection(StrEnum):
    IN = "IN"
    OUT = "OUT"
    INOUT = "INOUT"


class AccessKind(StrEnum):
    IMPLICIT_READ = "implicit read"  # DATA-READ-ACCESS
    IMPLICIT_WRITE = "implicit write"  # DATA-WRITE-ACCESS
    EXPLICIT_READ = "explicit read"  # DATA-RECEIVE-POINT-BY-ARGUMENT / -BY-VALUE, non-queued
    EXPLICIT_SEND = "explicit send"  # DATA-SEND-POINT
    QUEUED_RECEIVE = "queued receive"  # DATA-RECEIVE-POINT-BY-ARGUMENT on a queued element
    SYNC_SERVER_CALL = "synchronous server call"  # SYNCHRONOUS-SERVER-CALL-POINT
    ASYNC_SERVER_CALL = "asynchronous server call"  # ASYNCHRONOUS-SERVER-CALL-POINT
    MODE_ACCESS = "mode access"  # MODE-ACCESS-POINT
    MODE_SWITCH = "mode switch"  # MODE-SWITCH-POINT
    PARAMETER_ACCESS = "parameter access"  # PARAMETER-ACCESS
    OTHER = "other"


class TriggerKind(StrEnum):
    PERIODIC = "periodic"  # TIMING-EVENT
    DATA_RECEIVED = "on data reception"  # DATA-RECEIVED-EVENT
    DATA_RECEIVE_ERROR = "on data reception error"  # DATA-RECEIVE-ERROR-EVENT
    OPERATION_INVOKED = "on operation invocation"  # OPERATION-INVOKED-EVENT
    MODE_SWITCH = "on mode switch"  # SWC-MODE-SWITCH-EVENT
    INIT = "init"  # INIT-EVENT
    BACKGROUND = "background"  # BACKGROUND-EVENT
    DATA_SEND_COMPLETED = "on data send completion"  # DATA-SEND-COMPLETED-EVENT
    OTHER = "other"


class ConnectorKind(StrEnum):
    ASSEMBLY = "assembly"
    DELEGATION = "delegation"


class DataTypeLevel(StrEnum):
    APPLICATION = "application"
    IMPLEMENTATION = "implementation"


class DataTypeCategory(StrEnum):
    VALUE = "value"
    BOOLEAN = "boolean"
    ENUMERATION = "enumeration"
    ARRAY = "array"
    RECORD = "record"
    STRING = "string"
    TYPE_REFERENCE = "type-reference"
    OTHER = "other"


# --------------------------------------------------------------------------- objects


@dataclass(frozen=True, slots=True)
class Origin:
    """Where an object was read from. ``file`` is repo-relative with forward slashes."""

    file: str
    line: int | None = None
    uuid: str | None = None


NO_ORIGIN = Origin(file="")


@dataclass(frozen=True, slots=True)
class SourceFile:
    """Fingerprint of one loaded file. The writer compares it before it edits the file."""

    rel_path: str
    size: int
    mtime_ns: int
    sha256: str | None = None


@dataclass(frozen=True, slots=True)
class DataElement:
    name: str
    type_path: str | None
    is_queued: bool = False
    init_value: str | None = None


@dataclass(frozen=True, slots=True)
class Argument:
    name: str
    direction: ArgumentDirection
    type_path: str | None


@dataclass(frozen=True, slots=True)
class Operation:
    name: str
    arguments: tuple[Argument, ...] = ()
    possible_errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PortInterface:
    path: str
    name: str
    kind: InterfaceKind
    is_service: bool = False
    elements: tuple[DataElement, ...] = ()  # data elements, parameters, NV data or triggers
    operations: tuple[Operation, ...] = ()  # client-server only
    mode_group_path: str | None = None  # mode-switch only: the mode declaration group
    origin: Origin = NO_ORIGIN

    def element(self, name: str) -> DataElement | None:
        return next((e for e in self.elements if e.name == name), None)

    def operation(self, name: str) -> Operation | None:
        return next((o for o in self.operations if o.name == name), None)


@dataclass(frozen=True, slots=True)
class ComSpec:
    """Communication spec of one data element or operation of a port."""

    target: str
    init_value: str | None = None
    queue_length: int | None = None


@dataclass(frozen=True, slots=True)
class Port:
    path: str
    name: str
    swc_path: str
    direction: PortDirection
    interface_path: str | None
    com_specs: tuple[ComSpec, ...] = ()
    origin: Origin = NO_ORIGIN


@dataclass(frozen=True, slots=True)
class Runnable:
    path: str
    name: str
    swc_path: str
    symbol: str | None = None
    can_be_invoked_concurrently: bool = False
    min_start_interval_s: float | None = None
    origin: Origin = NO_ORIGIN


@dataclass(frozen=True, slots=True)
class AccessPoint:
    """One port access of a runnable. ``target`` is the data element or operation name."""

    path: str
    swc_path: str
    runnable_path: str
    port_path: str | None
    target: str | None
    kind: AccessKind
    origin: Origin = NO_ORIGIN


@dataclass(frozen=True, slots=True)
class Trigger:
    """One RTE event that starts a runnable."""

    path: str
    name: str
    swc_path: str
    runnable_path: str | None
    kind: TriggerKind
    port_path: str | None = None
    target: str | None = None  # data element, operation or mode name
    period_s: float | None = None
    origin: Origin = NO_ORIGIN


@dataclass(frozen=True, slots=True)
class TaskMapping:
    """Event-to-task mapping read from the ECUC file. Read-only for PortTool's Python code."""

    event_path: str
    task: str
    position: int | None = None


@dataclass(frozen=True, slots=True)
class Component:
    path: str
    name: str
    kind: ComponentKind
    owner: ArxmlOwner = ArxmlOwner.DAVINCI
    team: str | None = None
    behavior_path: str | None = None
    origin: Origin = NO_ORIGIN


@dataclass(frozen=True, slots=True)
class ComponentPrototype:
    """An instance of a component type inside a composition."""

    path: str
    name: str
    composition_path: str
    type_path: str | None
    origin: Origin = NO_ORIGIN


@dataclass(frozen=True, slots=True)
class ConnectorEnd:
    """``prototype_path`` is None for the composition's own (outer) port of a delegation."""

    prototype_path: str | None
    port_path: str | None


@dataclass(frozen=True, slots=True)
class Connector:
    path: str
    name: str
    composition_path: str
    kind: ConnectorKind
    provider: ConnectorEnd
    requester: ConnectorEnd
    origin: Origin = NO_ORIGIN


@dataclass(frozen=True, slots=True)
class Composition:
    path: str
    name: str
    origin: Origin = NO_ORIGIN


@dataclass(frozen=True, slots=True)
class DataType:
    path: str
    name: str
    level: DataTypeLevel
    category: DataTypeCategory
    base_type_path: str | None = None
    compu_method_path: str | None = None
    constraint_path: str | None = None
    unit_path: str | None = None
    element_type_paths: tuple[str, ...] = ()  # array: one entry; record: one per element
    element_names: tuple[str, ...] = ()  # record element names, parallel to element_type_paths
    array_size: int | None = None
    origin: Origin = NO_ORIGIN


@dataclass(frozen=True, slots=True)
class BaseType:
    path: str
    name: str
    size_bits: int | None = None
    encoding: str | None = None
    origin: Origin = NO_ORIGIN


@dataclass(frozen=True, slots=True)
class CompuMethod:
    path: str
    name: str
    category: str | None = None  # IDENTICAL, LINEAR, TEXTTABLE, ...
    factor: float | None = None
    offset: float | None = None
    enum_values: tuple[tuple[int, str], ...] = ()
    unit_path: str | None = None
    origin: Origin = NO_ORIGIN


@dataclass(frozen=True, slots=True)
class DataConstraint:
    path: str
    name: str
    lower: float | None = None
    upper: float | None = None
    origin: Origin = NO_ORIGIN


@dataclass(frozen=True, slots=True)
class Unit:
    path: str
    name: str
    display_name: str | None = None
    origin: Origin = NO_ORIGIN


@dataclass(frozen=True, slots=True)
class DataTypeMappingSet:
    path: str
    name: str
    pairs: tuple[tuple[str, str], ...] = ()  # (application type path, implementation type path)
    origin: Origin = NO_ORIGIN


@dataclass(frozen=True, slots=True)
class Constant:
    path: str
    name: str
    value: str | None = None  # textual form of the value specification
    origin: Origin = NO_ORIGIN


@dataclass(frozen=True, slots=True)
class PortInstance:
    """A port of one component prototype: the thing connectors attach to."""

    prototype: ComponentPrototype
    port: Port

    @property
    def key(self) -> tuple[str, str]:
        return (self.prototype.path, self.port.path)


# --------------------------------------------------------------------------- model


class _HasPath(Protocol):
    @property
    def path(self) -> str: ...


_T = TypeVar("_T", bound=_HasPath)
_K = TypeVar("_K")
_V = TypeVar("_V")


def _by_path(items: Iterable[_T]) -> Mapping[str, _T]:
    return MappingProxyType({item.path: item for item in items})


def _freeze(groups: Mapping[_K, list[_V]]) -> Mapping[_K, tuple[_V, ...]]:
    return MappingProxyType({key: tuple(values) for key, values in groups.items()})


class Model:
    """Immutable, indexed snapshot of the repo. Built once by the reader, then only read.

    All lookups are O(1) dictionary reads. Never loop over ``ports`` per request.
    """

    __slots__ = (
        "components",
        "ports",
        "interfaces",
        "runnables",
        "access_points",
        "triggers",
        "task_mappings",
        "tasks",
        "compositions",
        "prototypes",
        "connectors",
        "data_types",
        "base_types",
        "compu_methods",
        "constraints",
        "units",
        "mapping_sets",
        "constants",
        "files",
        "findings",
        "baseline_commit",
        "_components_by_name",
        "_ports_by_swc",
        "_port_by_swc_name",
        "_ports_by_interface",
        "_runnables_by_swc",
        "_runnable_by_swc_name",
        "_access_by_runnable",
        "_access_by_port",
        "_triggers_by_runnable",
        "_triggers_by_swc",
        "_prototypes_by_composition",
        "_prototypes_by_type",
        "_connectors_by_composition",
        "_connectors_by_end",
        "_hash",
    )

    def __init__(
        self,
        *,
        components: Iterable[Component] = (),
        ports: Iterable[Port] = (),
        interfaces: Iterable[PortInterface] = (),
        runnables: Iterable[Runnable] = (),
        access_points: Iterable[AccessPoint] = (),
        triggers: Iterable[Trigger] = (),
        task_mappings: Iterable[TaskMapping] = (),
        tasks: Iterable[str] = (),
        compositions: Iterable[Composition] = (),
        prototypes: Iterable[ComponentPrototype] = (),
        connectors: Iterable[Connector] = (),
        data_types: Iterable[DataType] = (),
        base_types: Iterable[BaseType] = (),
        compu_methods: Iterable[CompuMethod] = (),
        constraints: Iterable[DataConstraint] = (),
        units: Iterable[Unit] = (),
        mapping_sets: Iterable[DataTypeMappingSet] = (),
        constants: Iterable[Constant] = (),
        files: Iterable[SourceFile] = (),
        findings: Iterable[Finding] = (),
        baseline_commit: str | None = None,
    ) -> None:
        self.components: Mapping[str, Component] = _by_path(components)
        self.ports: Mapping[str, Port] = _by_path(ports)
        self.interfaces: Mapping[str, PortInterface] = _by_path(interfaces)
        self.runnables: Mapping[str, Runnable] = _by_path(runnables)
        self.access_points: Mapping[str, AccessPoint] = _by_path(access_points)
        self.triggers: Mapping[str, Trigger] = _by_path(triggers)
        self.task_mappings: Mapping[str, TaskMapping] = MappingProxyType(
            {m.event_path: m for m in task_mappings}
        )
        self.tasks: frozenset[str] = frozenset(tasks)
        self.compositions: Mapping[str, Composition] = _by_path(compositions)
        self.prototypes: Mapping[str, ComponentPrototype] = _by_path(prototypes)
        self.connectors: Mapping[str, Connector] = _by_path(connectors)
        self.data_types: Mapping[str, DataType] = _by_path(data_types)
        self.base_types: Mapping[str, BaseType] = _by_path(base_types)
        self.compu_methods: Mapping[str, CompuMethod] = _by_path(compu_methods)
        self.constraints: Mapping[str, DataConstraint] = _by_path(constraints)
        self.units: Mapping[str, Unit] = _by_path(units)
        self.mapping_sets: Mapping[str, DataTypeMappingSet] = _by_path(mapping_sets)
        self.constants: Mapping[str, Constant] = _by_path(constants)
        self.files: Mapping[str, SourceFile] = MappingProxyType({f.rel_path: f for f in files})
        self.findings: tuple[Finding, ...] = tuple(findings)
        self.baseline_commit: str | None = baseline_commit

        by_name: dict[str, list[Component]] = defaultdict(list)
        for component in self.components.values():
            by_name[component.name].append(component)
        self._components_by_name = _freeze(by_name)

        ports_by_swc: dict[str, list[Port]] = defaultdict(list)
        ports_by_interface: dict[str, list[Port]] = defaultdict(list)
        port_by_swc_name: dict[tuple[str, str], Port] = {}
        for port in self.ports.values():
            ports_by_swc[port.swc_path].append(port)
            port_by_swc_name[(port.swc_path, port.name)] = port
            if port.interface_path is not None:
                ports_by_interface[port.interface_path].append(port)
        self._ports_by_swc = _freeze(ports_by_swc)
        self._ports_by_interface = _freeze(ports_by_interface)
        self._port_by_swc_name: Mapping[tuple[str, str], Port] = MappingProxyType(port_by_swc_name)

        runnables_by_swc: dict[str, list[Runnable]] = defaultdict(list)
        runnable_by_swc_name: dict[tuple[str, str], Runnable] = {}
        for runnable in self.runnables.values():
            runnables_by_swc[runnable.swc_path].append(runnable)
            runnable_by_swc_name[(runnable.swc_path, runnable.name)] = runnable
        self._runnables_by_swc = _freeze(runnables_by_swc)
        self._runnable_by_swc_name: Mapping[tuple[str, str], Runnable] = MappingProxyType(
            runnable_by_swc_name
        )

        access_by_runnable: dict[str, list[AccessPoint]] = defaultdict(list)
        access_by_port: dict[str, list[AccessPoint]] = defaultdict(list)
        for access in self.access_points.values():
            access_by_runnable[access.runnable_path].append(access)
            if access.port_path is not None:
                access_by_port[access.port_path].append(access)
        self._access_by_runnable = _freeze(access_by_runnable)
        self._access_by_port = _freeze(access_by_port)

        triggers_by_runnable: dict[str, list[Trigger]] = defaultdict(list)
        triggers_by_swc: dict[str, list[Trigger]] = defaultdict(list)
        for trigger in self.triggers.values():
            triggers_by_swc[trigger.swc_path].append(trigger)
            if trigger.runnable_path is not None:
                triggers_by_runnable[trigger.runnable_path].append(trigger)
        self._triggers_by_runnable = _freeze(triggers_by_runnable)
        self._triggers_by_swc = _freeze(triggers_by_swc)

        protos_by_composition: dict[str, list[ComponentPrototype]] = defaultdict(list)
        protos_by_type: dict[str, list[ComponentPrototype]] = defaultdict(list)
        for prototype in self.prototypes.values():
            protos_by_composition[prototype.composition_path].append(prototype)
            if prototype.type_path is not None:
                protos_by_type[prototype.type_path].append(prototype)
        self._prototypes_by_composition = _freeze(protos_by_composition)
        self._prototypes_by_type = _freeze(protos_by_type)

        conns_by_composition: dict[str, list[Connector]] = defaultdict(list)
        conns_by_end: dict[tuple[str, str], list[Connector]] = defaultdict(list)
        for connector in self.connectors.values():
            conns_by_composition[connector.composition_path].append(connector)
            for end in (connector.provider, connector.requester):
                if end.port_path is not None:
                    conns_by_end[(end.prototype_path or "", end.port_path)].append(connector)
        self._connectors_by_composition = _freeze(conns_by_composition)
        self._connectors_by_end = _freeze(conns_by_end)

        digest = hashlib.sha256()
        for rel_path in sorted(self.files):
            source = self.files[rel_path]
            fingerprint = source.sha256 or f"{source.size}:{source.mtime_ns}"
            digest.update(f"{rel_path}|{fingerprint}\n".encode())
        self._hash: str = digest.hexdigest()

    # ---- components

    def components_named(self, name: str) -> tuple[Component, ...]:
        """Component types with this short name (short names can repeat across packages)."""
        return self._components_by_name.get(name, ())

    # ---- ports

    def ports_of(self, swc_path: str) -> tuple[Port, ...]:
        return self._ports_by_swc.get(swc_path, ())

    def find_port(self, swc_path: str, port_name: str) -> Port | None:
        return self._port_by_swc_name.get((swc_path, port_name))

    def ports_using(self, interface_path: str) -> tuple[Port, ...]:
        return self._ports_by_interface.get(interface_path, ())

    # ---- behavior

    def runnables_of(self, swc_path: str) -> tuple[Runnable, ...]:
        return self._runnables_by_swc.get(swc_path, ())

    def find_runnable(self, swc_path: str, runnable_name: str) -> Runnable | None:
        return self._runnable_by_swc_name.get((swc_path, runnable_name))

    def access_points_of_runnable(self, runnable_path: str) -> tuple[AccessPoint, ...]:
        return self._access_by_runnable.get(runnable_path, ())

    def access_points_of_port(self, port_path: str) -> tuple[AccessPoint, ...]:
        return self._access_by_port.get(port_path, ())

    def triggers_of(self, runnable_path: str) -> tuple[Trigger, ...]:
        return self._triggers_by_runnable.get(runnable_path, ())

    def triggers_in(self, swc_path: str) -> tuple[Trigger, ...]:
        return self._triggers_by_swc.get(swc_path, ())

    def task_of(self, event_path: str) -> TaskMapping | None:
        return self.task_mappings.get(event_path)

    # ---- compositions

    def prototypes_in(self, composition_path: str) -> tuple[ComponentPrototype, ...]:
        return self._prototypes_by_composition.get(composition_path, ())

    def prototypes_of_type(self, type_path: str) -> tuple[ComponentPrototype, ...]:
        return self._prototypes_by_type.get(type_path, ())

    def connectors_in(self, composition_path: str) -> tuple[Connector, ...]:
        return self._connectors_by_composition.get(composition_path, ())

    def connectors_at(self, prototype_path: str | None, port_path: str) -> tuple[Connector, ...]:
        """Connectors attached to this port of this prototype (None = the composition's port)."""
        return self._connectors_by_end.get((prototype_path or "", port_path), ())

    def port_instances(self) -> Iterator[PortInstance]:
        """Every port of every component prototype."""
        for prototype in self.prototypes.values():
            if prototype.type_path is None:
                continue
            for port in self.ports_of(prototype.type_path):
                yield PortInstance(prototype=prototype, port=port)

    def unconnected(self) -> tuple[PortInstance, ...]:
        """Port instances with no connector. Computed on each call: cache the result."""
        return tuple(
            instance
            for instance in self.port_instances()
            if not self.connectors_at(instance.prototype.path, instance.port.path)
        )

    # ---- identity

    @property
    def model_hash(self) -> str:
        """Hash of the loaded files' fingerprints. Changes when any loaded file changes."""
        return self._hash
