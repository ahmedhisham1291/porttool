"""The repo model plus everything the current import adds to or removes from it. Internal.

One ``World`` lives for one ``validate()`` call. Every extra index is built at most once
per call, on first use; no request scans all ports or all connectors of the model.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from functools import cached_property
from typing import Protocol

from porttool.core.config import ProjectConfig
from porttool.core.models import (
    AccessPoint,
    ArxmlOwner,
    BaseType,
    Component,
    ComponentKind,
    ComponentPrototype,
    Composition,
    Connector,
    DataType,
    InterfaceKind,
    Model,
    Port,
    PortInterface,
    Trigger,
    TriggerKind,
    Unit,
)
from porttool.core.operations import AnyOperation, Route
from porttool.core.pending import PendingEntry
from porttool.core.results import Finding
from porttool.core.rowid import row_id
from porttool.core.sheets import Sheet
from porttool.validator import codes
from porttool.validator._state import Req
from porttool.validator._typeinfo import TypeInfo, info_from_model
from porttool.validator.names import NameChecker

#: Interface kinds that behave like sender-receiver (data elements, PR ports allowed).
SR_STYLE: frozenset[InterfaceKind] = frozenset(
    {InterfaceKind.SENDER_RECEIVER, InterfaceKind.NV_DATA}
)

_BASE_FIELDS = frozenset(
    {"id", "requester", "comment", "route", "source_file", "source_sheet", "source_row"}
)


class Access(StrEnum):
    """What PortTool may do with a SWC."""

    EDITABLE = "editable"
    MODEL = "model"
    LOCKED = "locked"
    SERVICE = "service"
    COMPOSITION = "composition"


class PortOrigin(StrEnum):
    MODEL = "model"  # exists in the repo
    NEW = "new"  # requested by a row of this import
    PENDING = "pending"  # waits in the pending queue for a model owner


@dataclass(frozen=True, slots=True)
class PortRef:
    """A port as the import sees it: existing, requested now, or awaited from a model owner."""

    port: Port
    origin: PortOrigin
    route: Route = Route.APPLY
    req: Req | None = None

    @property
    def exists(self) -> bool:
        return self.origin is PortOrigin.MODEL


class _Named(Protocol):
    @property
    def path(self) -> str: ...

    @property
    def name(self) -> str: ...


def parent_of(path: str) -> str:
    """Path of the package or object that contains ``path``."""
    return path.rsplit("/", 1)[0]


def last_of(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def scope_clash(scope: dict[str, str], name: str) -> tuple[str, str] | None:
    """``(code, existing name)`` when ``name`` is taken in ``scope`` (lower-case -> name)."""
    existing = scope.get(name.lower())
    if existing is None:
        return None
    return (codes.NAME_DUPLICATE if existing == name else codes.NAME_CASE_CLASH, existing)


def operation_signature(op: AnyOperation) -> dict[str, object]:
    """The content of an operation without id, route and where it came from."""
    return op.model_dump(exclude=set(_BASE_FIELDS))


def _by_name(items: Iterable[_Named]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = defaultdict(list)
    for item in items:
        index[item.name].append(item.path)
    return dict(index)


class World:
    """Model, configuration, lazily built indexes, and the overlay of the current import."""

    def __init__(self, model: Model, config: ProjectConfig, now_utc: str) -> None:
        self.model = model
        self.config = config
        self.now_utc = now_utc
        self.names = NameChecker(config.naming)
        #: Findings not tied to one request (configuration, pending queue).
        self.findings: list[Finding] = []

        # ---- overlay: what this import creates, changes and deletes
        self.new_types: dict[str, TypeInfo] = {}
        self.new_type_reqs: dict[str, Req] = {}
        self.failed_types: dict[str, Req] = {}
        self.new_interfaces: dict[str, PortInterface] = {}
        self.new_interface_reqs: dict[str, Req] = {}
        self.failed_interfaces: dict[str, Req] = {}
        self.new_ports: dict[tuple[str, str], PortRef] = {}
        self.pending_ports: dict[tuple[str, str], PortRef] = {}
        self.deleted_ports: dict[str, Req] = {}
        self.modified_ports: dict[str, tuple[Req, str | None]] = {}
        self.deleted_connectors: dict[str, Req] = {}
        self.deleted_triggers: dict[str, Req] = {}
        self.deleted_access: dict[str, Req] = {}
        self.new_runnables: dict[tuple[str, str], Req] = {}
        #: Ports that one row deletes and another row uses: every user is rejected.
        self.contested_ports: set[str] = set()
        #: Pending entries that stay in the queue, by operation id.
        self.pending_by_id: dict[str, PendingEntry] = {}
        #: Held connectors released by this run, by operation id.
        self.released: dict[str, AnyOperation] = {}

        self._broken_patterns: set[str] = set()
        self._type_info: dict[str, TypeInfo | None] = {}
        self._port_scopes: dict[str, dict[str, str]] = {}
        self._behavior_scopes: dict[str, dict[str, str]] = {}
        self._composition_scopes: dict[str, dict[str, str]] = {}
        self._server_triggers: dict[str, dict[tuple[str, str], list[Trigger]]] = {}
        self._port_triggers: dict[str, dict[str, list[Trigger]]] = {}

    # ------------------------------------------------------------------ name indexes

    @cached_property
    def interfaces_by_name(self) -> dict[str, list[str]]:
        return _by_name(self.model.interfaces.values())

    @cached_property
    def types_by_name(self) -> dict[str, list[str]]:
        return _by_name(self.model.data_types.values())

    @cached_property
    def base_types_by_name(self) -> dict[str, list[str]]:
        return _by_name(self.model.base_types.values())

    @cached_property
    def compositions_by_name(self) -> dict[str, list[str]]:
        return _by_name(self.model.compositions.values())

    @cached_property
    def units_by_text(self) -> dict[str, list[str]]:
        index: dict[str, list[str]] = defaultdict(list)
        for unit in self.model.units.values():
            index[unit.name].append(unit.path)
            if unit.display_name and unit.display_name != unit.name:
                index[unit.display_name].append(unit.path)
        return dict(index)

    @cached_property
    def prototypes_by_name(self) -> dict[str, list[ComponentPrototype]]:
        index: dict[str, list[ComponentPrototype]] = defaultdict(list)
        for prototype in self.model.prototypes.values():
            index[prototype.name].append(prototype)
        return dict(index)

    @cached_property
    def prototype_in_composition(self) -> dict[tuple[str, str], ComponentPrototype]:
        return {(p.composition_path, p.name): p for p in self.model.prototypes.values()}

    @cached_property
    def mode_groups_by_name(self) -> dict[str, list[str]]:
        """Mode declaration groups known from the existing mode-switch interfaces."""
        index: dict[str, list[str]] = defaultdict(list)
        for interface in self.model.interfaces.values():
            path = interface.mode_group_path
            if path and path not in index[last_of(path)]:
                index[last_of(path)].append(path)
        return dict(index)

    @cached_property
    def _package_scopes(self) -> dict[str, dict[str, str]]:
        scopes: dict[str, dict[str, str]] = defaultdict(dict)
        model = self.model
        groups: tuple[Iterable[_Named], ...] = (
            model.components.values(),
            model.compositions.values(),
            model.interfaces.values(),
            model.data_types.values(),
            model.base_types.values(),
            model.compu_methods.values(),
            model.constraints.values(),
            model.units.values(),
            model.mapping_sets.values(),
            model.constants.values(),
        )
        for group in groups:
            for item in group:
                scopes[parent_of(item.path)].setdefault(item.name.lower(), item.name)
        return dict(scopes)

    def package_scope(self, package_path: str) -> dict[str, str]:
        """Names of the loaded objects directly inside a package (lower-case -> name)."""
        return self._package_scopes.get(package_path, {})

    # ------------------------------------------------------------------ SWC scopes

    def port_scope(self, swc_path: str) -> dict[str, str]:
        """Names that a new port of this SWC must not repeat: its ports and its behavior."""
        scope = self._port_scopes.get(swc_path)
        if scope is None:
            scope = {port.name.lower(): port.name for port in self.model.ports_of(swc_path)}
            component = self.model.components.get(swc_path)
            if component is not None and component.behavior_path:
                behavior = last_of(component.behavior_path)
                scope.setdefault(behavior.lower(), behavior)
            self._port_scopes[swc_path] = scope
        return scope

    def behavior_scope(self, swc_path: str) -> dict[str, str]:
        """Names inside the internal behavior of this SWC: runnables and events."""
        scope = self._behavior_scopes.get(swc_path)
        if scope is None:
            scope = {r.name.lower(): r.name for r in self.model.runnables_of(swc_path)}
            for trigger in self.model.triggers_in(swc_path):
                scope.setdefault(trigger.name.lower(), trigger.name)
            self._behavior_scopes[swc_path] = scope
        return scope

    def composition_scope(self, composition_path: str) -> dict[str, str]:
        """Names inside a composition: connectors, component prototypes and outer ports."""
        scope = self._composition_scopes.get(composition_path)
        if scope is None:
            scope = {c.name.lower(): c.name for c in self.model.connectors_in(composition_path)}
            for prototype in self.model.prototypes_in(composition_path):
                scope.setdefault(prototype.name.lower(), prototype.name)
            for port in self.model.ports_of(composition_path):
                scope.setdefault(port.name.lower(), port.name)
            self._composition_scopes[composition_path] = scope
        return scope

    def server_triggers(self, swc_path: str) -> dict[tuple[str, str], list[Trigger]]:
        """Operation-invoked events of a SWC by ``(port path, operation)``."""
        index = self._server_triggers.get(swc_path)
        if index is None:
            built: dict[tuple[str, str], list[Trigger]] = defaultdict(list)
            for trigger in self.model.triggers_in(swc_path):
                if (
                    trigger.kind is TriggerKind.OPERATION_INVOKED
                    and trigger.port_path
                    and trigger.target
                ):
                    built[(trigger.port_path, trigger.target)].append(trigger)
            index = dict(built)
            self._server_triggers[swc_path] = index
        return index

    def triggers_on_port(self, swc_path: str, port_path: str) -> list[Trigger]:
        """Events of a SWC that refer to one of its ports."""
        index = self._port_triggers.get(swc_path)
        if index is None:
            built: dict[str, list[Trigger]] = defaultdict(list)
            for trigger in self.model.triggers_in(swc_path):
                if trigger.port_path:
                    built[trigger.port_path].append(trigger)
            index = dict(built)
            self._port_triggers[swc_path] = index
        return index.get(port_path, [])

    # ------------------------------------------------------------------ RowID lookups

    @cached_property
    def ports_by_rowid(self) -> dict[str, Port]:
        return {row_id(Sheet.PORTS, port.path): port for port in self.model.ports.values()}

    @cached_property
    def connectors_by_rowid(self) -> dict[str, Connector]:
        return {row_id(Sheet.CONNECTIONS, c.path): c for c in self.model.connectors.values()}

    @cached_property
    def access_by_rowid(self) -> dict[str, AccessPoint]:
        return {row_id(Sheet.RUNNABLE_ACCESS, a.path): a for a in self.model.access_points.values()}

    @cached_property
    def triggers_by_rowid(self) -> dict[str, Trigger]:
        return {row_id(Sheet.RUNNABLE_TRIGGERS, t.path): t for t in self.model.triggers.values()}

    # ------------------------------------------------------------------ components

    def resolve_swc(self, req: Req, text: str) -> Component | None:
        """SWC by short name or absolute path. Adds the Finding when it cannot be resolved."""
        if text.startswith("/"):
            component = self.model.components.get(text)
            if component is None:
                req.error(codes.REF_UNKNOWN_SWC, f"SWC '{text}' does not exist in the repo.", text)
            return component
        found = self.model.components_named(text)
        if len(found) == 1:
            return found[0]
        if not found:
            req.error(codes.REF_UNKNOWN_SWC, f"SWC '{text}' does not exist in the repo.")
            return None
        paths = ", ".join(sorted(component.path for component in found))
        req.error(
            codes.REF_AMBIGUOUS_SWC,
            f"Several SWCs are named '{text}' ({paths}). Enter the absolute path instead.",
        )
        return None

    def unique_swc(self, text: str) -> Component | None:
        """SWC by short name or absolute path, without a Finding. None when not unique."""
        if text.startswith("/"):
            return self.model.components.get(text)
        found = self.model.components_named(text)
        return found[0] if len(found) == 1 else None

    def unique_composition(self, text: str) -> Composition | None:
        """Composition by short name or absolute path, without a Finding."""
        if text.startswith("/"):
            return self.model.compositions.get(text)
        paths = self.compositions_by_name.get(text, [])
        return self.model.compositions[paths[0]] if len(paths) == 1 else None

    def access_of(self, component: Component) -> Access:
        owners = (component.owner, self.config.owner_of(component.name))
        if ArxmlOwner.LOCKED in owners:
            return Access.LOCKED
        if component.kind is ComponentKind.SERVICE:
            return Access.SERVICE
        if component.kind is ComponentKind.COMPOSITION or component.path in self.model.compositions:
            return Access.COMPOSITION
        if ArxmlOwner.MODEL in owners:
            return Access.MODEL
        return Access.EDITABLE

    def route_for(self, req: Req, component: Component) -> Route | None:
        """Route of a request on ``component``; None (with a Finding) when it is read-only."""
        access = self.access_of(component)
        if access is Access.EDITABLE:
            return Route.APPLY
        if access is Access.MODEL:
            return Route.MODEL
        if access is Access.LOCKED:
            req.error(
                codes.OWN_LOCKED,
                f"SWC {component.name} is locked (supplier, service or generated component) "
                "and is never edited by PortTool.",
                component.path,
            )
        elif access is Access.SERVICE:
            req.error(
                codes.OWN_SERVICE,
                f"{component.name} is a service SWC. Service SWCs are generated by the basic "
                "software configuration and are read-only.",
                component.path,
            )
        else:
            req.error(
                codes.OWN_COMPOSITION,
                f"{component.name} is a composition. A composition used as SWC is read-only "
                "in v1; only connectors inside it can be requested.",
                component.path,
            )
        return None

    def team_of(self, component: Component) -> str | None:
        return self.config.team_of(component.name) or component.team

    # ------------------------------------------------------------------ interfaces

    def interface(self, path: str | None) -> PortInterface | None:
        if path is None:
            return None
        return self.model.interfaces.get(path) or self.new_interfaces.get(path)

    def resolve_interface(self, req: Req, text: str) -> PortInterface | None:
        """Interface by short name or absolute path: existing, or created by this import."""
        package = self.config.packages.interfaces
        if text.startswith("/"):
            paths = [text] if self.interface(text) is not None else []
            failed = self.failed_interfaces.get(text)
        else:
            new_path = f"{package}/{text}"
            paths = list(self.interfaces_by_name.get(text, ()))
            if new_path in self.new_interfaces and new_path not in paths:
                paths.append(new_path)
            failed = self.failed_interfaces.get(new_path)
        if len(paths) == 1:
            path = paths[0]
            creator = self.new_interface_reqs.get(path)
            if creator is not None and path not in self.model.interfaces:
                req.depends.append(creator)
            return self.interface(path)
        if len(paths) > 1:
            req.error(
                codes.REF_AMBIGUOUS_INTERFACE,
                f"Several interfaces are named '{text}' ({', '.join(sorted(paths))}). "
                "Enter the absolute path instead.",
            )
        elif failed is not None:
            req.error(
                codes.REF_REJECTED_DEPENDENCY,
                f"Interface '{text}' is requested in {failed.where}, but that request was "
                "rejected.",
            )
        else:
            req.error(
                codes.REF_UNKNOWN_INTERFACE,
                f"Interface '{text}' does not exist in the repo and is not created by this import.",
            )
        return None

    # ------------------------------------------------------------------ data types

    def type_exists(self, path: str) -> bool:
        return path in self.model.data_types or path in self.new_types

    def type_info(self, path: str | None) -> TypeInfo | None:
        if path is None:
            return None
        new = self.new_types.get(path)
        if new is not None:
            return new
        if path not in self._type_info:
            self._type_info[path] = info_from_model(self.model, path)
        return self._type_info[path]

    def resolve_type(self, req: Req, text: str, what: str) -> str | None:
        """Data type path by short name or absolute path: existing, or created by this import."""
        package = self.config.packages.data_types
        if text.startswith("/"):
            paths = [text] if self.type_exists(text) else []
            failed = self.failed_types.get(text)
        else:
            new_path = f"{package}/{text}"
            paths = list(self.types_by_name.get(text, ()))
            if new_path in self.new_types and new_path not in paths:
                paths.append(new_path)
            failed = self.failed_types.get(new_path)
        if len(paths) == 1:
            creator = self.new_type_reqs.get(paths[0])
            if creator is not None and paths[0] not in self.model.data_types:
                req.depends.append(creator)
            return paths[0]
        if len(paths) > 1:
            req.error(
                codes.REF_AMBIGUOUS_DATA_TYPE,
                f"Several data types are named '{text}' ({', '.join(sorted(paths))}), so the "
                f"type of {what} is not clear. Enter the absolute path instead.",
            )
        elif failed is not None:
            req.error(
                codes.REF_REJECTED_DEPENDENCY,
                f"Data type '{text}' of {what} is requested in {failed.where}, but that "
                "request was rejected.",
            )
        else:
            req.error(
                codes.REF_UNKNOWN_DATA_TYPE,
                f"Data type '{text}' of {what} does not exist in the repo and is not created "
                "by this import.",
            )
        return None

    def data_type(self, path: str) -> DataType | None:
        return self.model.data_types.get(path)

    def base_type(self, text: str) -> tuple[BaseType | None, int]:
        """Base type by short name or path, and how many matched."""
        if text.startswith("/"):
            found = self.model.base_types.get(text)
            return found, 1 if found is not None else 0
        paths = self.base_types_by_name.get(text, [])
        return (self.model.base_types[paths[0]] if len(paths) == 1 else None), len(paths)

    def unit(self, text: str) -> tuple[Unit | None, int]:
        """Unit by short name, display name or path, and how many matched."""
        if text.startswith("/"):
            found = self.model.units.get(text)
            return found, 1 if found is not None else 0
        paths = self.units_by_text.get(text, [])
        return (self.model.units[paths[0]] if len(paths) == 1 else None), len(paths)

    # ------------------------------------------------------------------ compositions

    def composition(self, path: str) -> Composition | None:
        return self.model.compositions.get(path)

    # ------------------------------------------------------------------ ports and runnables

    def find_port(self, swc_path: str, name: str) -> PortRef | None:
        port = self.model.find_port(swc_path, name)
        if port is not None:
            return PortRef(port=port, origin=PortOrigin.MODEL)
        key = (swc_path, name)
        return self.new_ports.get(key) or self.pending_ports.get(key)

    def interface_path_of(self, ref: PortRef) -> str | None:
        """Interface of the port after this import (a MODIFY row may change it)."""
        if ref.exists:
            change = self.modified_ports.get(ref.port.path)
            if change is not None and change[1] is not None and not change[0].rejected:
                return change[1]
        return ref.port.interface_path

    def resolve_port(self, req: Req, component: Component, name: str) -> PortRef | None:
        """Port of ``component`` by name: existing, requested by this import, or pending.

        Adds the Finding when the port is unknown or when another row deletes it.
        """
        ref = self.find_port(component.path, name)
        if ref is None:
            req.error(
                codes.REF_UNKNOWN_PORT,
                f"SWC {component.name} has no port '{name}', and no row of this import or "
                "entry of the pending queue requests it. Add it on the Ports sheet first.",
                f"{component.path}/{name}",
            )
            return None
        if ref.exists:
            deleter = self.deleted_ports.get(ref.port.path)
            if deleter is not None and (
                not deleter.root().rejected or ref.port.path in self.contested_ports
            ):
                self.contested_ports.add(ref.port.path)
                req.error(
                    codes.CFL_DELETED_PORT,
                    f"Port {component.name}.{name} is deleted by {deleter.where}. Both "
                    "requests are rejected; decide which one is wanted.",
                    ref.port.path,
                )
                deleter.root().error(
                    codes.CFL_DELETED_PORT,
                    f"Port {component.name}.{name} is used by the request in {req.where}. "
                    "Both requests are rejected; decide which one is wanted.",
                    ref.port.path,
                )
                return None
            change = self.modified_ports.get(ref.port.path)
            if change is not None and change[1] is not None:
                req.depends.append(change[0])
        elif ref.req is not None:
            req.depends.append(ref.req)
        return ref

    def port_interface(self, req: Req, ref: PortRef, owner_name: str) -> PortInterface | None:
        """Interface of a port; adds the Finding when the repo does not contain it."""
        interface = self.interface(self.interface_path_of(ref))
        if interface is None:
            req.error(
                codes.REF_PORT_WITHOUT_INTERFACE,
                f"Port {owner_name}.{ref.port.name} refers to interface "
                f"'{ref.port.interface_path or '(none)'}', which is not loaded. The request "
                "cannot be checked.",
                ref.port.path,
            )
        return interface

    def has_runnable(self, req: Req, swc_path: str, name: str) -> bool:
        """True when the runnable exists or is a server runnable created by this import."""
        if self.model.find_runnable(swc_path, name) is not None:
            return True
        creator = self.new_runnables.get((swc_path, name))
        if creator is not None and not creator.root().rejected:
            req.depends.append(creator)
            return True
        return False

    # ------------------------------------------------------------------ name patterns

    def add_finding(self, code: str, message: str, object_path: str | None = None) -> None:
        """Add a finding that is not tied to one request, with the code's default severity."""
        self.findings.append(
            Finding(
                code=code,
                severity=codes.severity_of(code),
                message=message,
                object_path=object_path,
            )
        )

    def fill_pattern(self, req: Req, setting: str, pattern: str, **fields: str) -> str | None:
        """Fill a name pattern of the configuration. None (with Findings) when it is broken."""
        try:
            return pattern.format(**fields)
        except (KeyError, IndexError, ValueError, AttributeError) as error:
            detail = (
                f"The name pattern {setting} = '{pattern}' of the project configuration cannot "
                f"be filled in ({type(error).__name__}: {error}). Allowed placeholders: "
                f"{', '.join('{' + key + '}' for key in fields)}."
            )
            if setting not in self._broken_patterns:
                self._broken_patterns.add(setting)
                self.add_finding(codes.CFG_NAME_PATTERN, detail)
            req.error(codes.CFG_NAME_PATTERN, detail)
            return None

    # ------------------------------------------------------------------ pending queue

    def check_pending(self, req: Req, op: AnyOperation) -> bool:
        """Compare ``op`` with the pending entry of the same id, if there is one.

        Returns False (with a Finding) when the pending request differs from this one.
        """
        entry = self.pending_by_id.get(op.id)
        if entry is None:
            return True
        if operation_signature(entry.operation) != operation_signature(op):
            req.error(
                codes.CFL_PENDING,
                "A different request for the same object has been in the pending queue since "
                f"{entry.created_utc} (requester: {entry.operation.requester or 'unknown'}). "
                "Close or correct that entry first.",
            )
            return False
        req.info(
            codes.PEND_ALREADY_QUEUED,
            f"This request has been in the pending queue since {entry.created_utc} "
            f"({entry.state.value}). It is not queued a second time.",
        )
        return True
