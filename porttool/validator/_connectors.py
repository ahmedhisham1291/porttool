"""Connections sheet: ADD rows become ``AddConnector``; held connectors are released. Internal."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from porttool.core.models import (
    Component,
    ComponentPrototype,
    Connector,
    InterfaceKind,
    PortDirection,
)
from porttool.core.operations import AddConnector, Route
from porttool.core.results import RawRequestRow, RowRef, Severity
from porttool.core.sheets import Action, Sheet
from porttool.validator import _ids, codes
from porttool.validator._changes import composition_editable
from porttool.validator._common import require, settle
from porttool.validator._compat import incompatibility
from porttool.validator._pending import QueueItem, describe_operation
from porttool.validator._state import Req
from porttool.validator._world import (
    SR_STYLE,
    PortOrigin,
    PortRef,
    World,
    last_of,
    scope_clash,
)
from porttool.validator.names import NameKind

_SHEET = Sheet.CONNECTIONS

InstanceKey = tuple[str, str]  # (prototype path, port path)


@dataclass(slots=True)
class _Side:
    prototype: ComponentPrototype
    component: Component
    port: PortRef

    @property
    def key(self) -> InstanceKey:
        return (self.prototype.path, self.port.port.path)

    @property
    def label(self) -> str:
        return f"{self.prototype.name}.{self.port.port.name}"


@dataclass(slots=True)
class _Candidate:
    req: Req
    composition_path: str
    provider: _Side
    requester: _Side
    name: str
    explicit_name: bool

    @property
    def op_id(self) -> str:
        return _ids.add_connector(
            self.composition_path,
            self.provider.prototype.name,
            self.provider.port.port.name,
            self.requester.prototype.name,
            self.requester.port.port.name,
        )


class Wiring:
    """Who is connected to whom after the import: the model, minus deletes, plus new connectors."""

    def __init__(self, world: World) -> None:
        self._world = world
        self._new_providers: dict[InstanceKey, set[InstanceKey]] = {}
        self._new_requesters: dict[InstanceKey, set[InstanceKey]] = {}
        #: Connector names added by this import, per composition (lower-case -> name).
        self.new_names: dict[str, dict[str, str]] = {}

    def _stays(self, connector: Connector) -> bool:
        deleter = self._world.deleted_connectors.get(connector.path)
        return deleter is None or deleter.root().rejected

    def providers_of(self, requester: InstanceKey) -> set[InstanceKey]:
        found = set(self._new_providers.get(requester, ()))
        for connector in self._world.model.connectors_at(requester[0] or None, requester[1]):
            end = connector.requester
            if (end.prototype_path or "", end.port_path) == requester and self._stays(connector):
                other = connector.provider
                if other.port_path is not None:
                    found.add((other.prototype_path or "", other.port_path))
        return found

    def requesters_of(self, provider: InstanceKey) -> set[InstanceKey]:
        found = set(self._new_requesters.get(provider, ()))
        for connector in self._world.model.connectors_at(provider[0] or None, provider[1]):
            end = connector.provider
            if (end.prototype_path or "", end.port_path) == provider and self._stays(connector):
                other = connector.requester
                if other.port_path is not None:
                    found.add((other.prototype_path or "", other.port_path))
        return found

    def add(
        self, composition_path: str, name: str, provider: InstanceKey, requester: InstanceKey
    ) -> None:
        self._new_providers.setdefault(requester, set()).add(provider)
        self._new_requesters.setdefault(provider, set()).add(requester)
        self.new_names.setdefault(composition_path, {})[name.lower()] = name


# ---------------------------------------------------------------------- reading rows


def _composition(world: World, req: Req, provider: str, requester: str) -> str | None:
    """Composition of a row: the Composition cell, or the one that holds both components."""
    text = req.cell("composition")
    if text:
        if text.startswith("/"):
            paths = [text] if text in world.model.compositions else []
        else:
            paths = world.compositions_by_name.get(text, [])
        if len(paths) == 1:
            return paths[0]
        if paths:
            req.error(
                codes.REF_AMBIGUOUS_COMPOSITION,
                f"Several compositions are named '{text}' ({', '.join(sorted(paths))}). Enter "
                "the absolute path instead.",
            )
        else:
            req.error(
                codes.REF_UNKNOWN_COMPOSITION, f"Composition '{text}' does not exist in the repo."
            )
        return None
    homes: list[set[str]] = []
    for name in (provider, requester):
        found = {p.composition_path for p in world.prototypes_by_name.get(name, [])}
        if not found:
            req.error(
                codes.REF_UNKNOWN_PROTOTYPE,
                f"No composition has a component named '{name}'. Enter the component name as "
                "the Connections sheet shows it.",
            )
        homes.append(found)
    if req.rejected:
        return None
    common = sorted(homes[0] & homes[1])
    if len(common) == 1:
        return common[0]
    if not common:
        req.error(
            codes.CON_NO_COMPOSITION,
            f"Components {provider} and {requester} are not in the same composition. v1 "
            "connects only components of one composition (no delegation connectors).",
        )
    else:
        req.error(
            codes.CON_MANY_COMPOSITIONS,
            f"Components {provider} and {requester} are both in several compositions "
            f"({', '.join(common)}). Name one in the Composition cell.",
        )
    return None


def _parse(world: World, req: Req) -> _Candidate | None:
    keys = ("provider_swc", "provider_port", "requester_swc", "requester_port")
    if not require(req, _SHEET, *keys):
        return None
    provider_name = req.cell("provider_swc")
    requester_name = req.cell("requester_swc")
    composition_path = _composition(world, req, provider_name, requester_name)
    if composition_path is None:
        return None
    sides: list[_Side] = []
    for prototype_name, port_key in (
        (provider_name, "provider_port"),
        (requester_name, "requester_port"),
    ):
        prototype = world.prototype_in_composition.get((composition_path, prototype_name))
        if prototype is None:
            req.error(
                codes.REF_UNKNOWN_PROTOTYPE,
                f"Composition {last_of(composition_path)} has no component named "
                f"'{prototype_name}'. Enter the component name as the Connections sheet "
                "shows it.",
                composition_path,
            )
            continue
        component = world.model.components.get(prototype.type_path or "")
        if component is None:
            req.error(
                codes.REF_UNKNOWN_SWC,
                f"The SWC type of component {prototype_name} "
                f"('{prototype.type_path or '(none)'}') is not loaded.",
                prototype.path,
            )
            continue
        port = world.resolve_port(req, component, req.cell(port_key))
        if port is not None:
            sides.append(_Side(prototype, component, port))
    if not composition_editable(world, req, composition_path):
        return None
    name: str | None = req.cell("connector_name")
    explicit = bool(name)
    if not explicit:
        name = world.fill_pattern(
            req,
            "connector_pattern",
            world.config.naming.connector_pattern,
            provider=provider_name,
            provider_port=req.cell("provider_port"),
            requester=requester_name,
            requester_port=req.cell("requester_port"),
        )
    if name:
        problems = world.names.check(name, NameKind.CONNECTOR)
        if problems and not explicit:
            problems = [
                (
                    code,
                    f"{message} The name was built from the project pattern; enter a name "
                    "in the Connector name cell.",
                )
                for code, message in problems
            ]
        req.problems(problems)
    if req.rejected or len(sides) != 2 or not name:
        return None
    return _Candidate(req, composition_path, sides[0], sides[1], name, explicit)


# ---------------------------------------------------------------------- rules


def _describe(world: World, key: InstanceKey) -> str:
    prototype = world.model.prototypes.get(key[0])
    return f"{prototype.name if prototype is not None else '(composition)'}.{last_of(key[1])}"


def _check_multiplicity(
    world: World, wiring: Wiring, item: _Candidate, kind: InterfaceKind
) -> None:
    req = item.req
    provider = item.provider.key
    requester = item.requester.key
    providers = wiring.providers_of(requester) | {provider}
    others = ", ".join(sorted(_describe(world, key) for key in providers if key != provider))
    if kind not in SR_STYLE:
        if len(providers) > 1:
            if kind is InterfaceKind.CLIENT_SERVER:
                req.error(
                    codes.MUL_CLIENT,
                    f"Client port {item.requester.label} is already connected to server "
                    f"{others}. A client port has exactly one server.",
                    item.requester.port.port.path,
                )
            else:
                req.error(
                    codes.MUL_SINGLE_PROVIDER,
                    f"Port {item.requester.label} ({kind.value}) is already fed by {others}. "
                    "A requester port of this kind accepts one provider only.",
                    item.requester.port.port.path,
                )
        return
    requesters = wiring.requesters_of(provider) | {requester}
    many_to_many = False
    if len(providers) > 1:
        many_to_many = any(
            len(wiring.requesters_of(key) | ({requester} if key == provider else set())) > 1
            for key in providers
        )
    if not many_to_many and len(requesters) > 1:
        many_to_many = any(
            len(wiring.providers_of(key) | ({provider} if key == requester else set())) > 1
            for key in requesters
        )
    if many_to_many:
        req.error(
            codes.MUL_N_TO_M,
            f"Connecting {item.provider.label} to {item.requester.label} would give several "
            "senders and several receivers on the same data (n:m). Sender-receiver "
            "communication is 1:n or n:1 only.",
            item.requester.port.port.path,
        )
    elif len(providers) > 1:
        req.warn(
            codes.MUL_N_TO_ONE,
            f"Receiver {item.requester.label} is also fed by {others}: several senders write "
            "to one receiver (n:1). This is allowed; check that it is intended.",
            item.requester.port.port.path,
        )


def _existing_connector(world: World, item: _Candidate) -> Connector | None:
    if not (item.provider.port.exists and item.requester.port.exists):
        return None
    provider, requester = item.provider.key, item.requester.key
    for connector in world.model.connectors_at(provider[0], provider[1]):
        if (connector.provider.prototype_path, connector.provider.port_path) == provider and (
            connector.requester.prototype_path,
            connector.requester.port_path,
        ) == requester:
            return connector
    return None


def _check_name(world: World, wiring: Wiring, item: _Candidate) -> None:
    req = item.req
    composition = item.composition_path
    clash = scope_clash(world.composition_scope(composition), item.name)
    if clash is not None and clash[0] == codes.NAME_DUPLICATE:
        deleter = world.deleted_connectors.get(f"{composition}/{item.name}")
        if deleter is not None and not deleter.root().rejected:
            req.depends.append(deleter)  # the name is free once that connector is deleted
            clash = None
    clash = clash or scope_clash(wiring.new_names.get(composition, {}), item.name)
    if clash is None:
        return
    code, other = clash
    hint = "" if item.explicit_name else " Enter another name in the Connector name cell."
    req.error(
        code,
        f"Connector name '{item.name}' is not free in composition {last_of(composition)}: "
        + (
            "another connector or component already has this name."
            if code == codes.NAME_DUPLICATE
            else f"'{other}' differs only by letter case."
        )
        + hint,
        f"{composition}/{item.name}",
    )


def _add(world: World, wiring: Wiring, item: _Candidate) -> AddConnector | None:
    """Apply every connector rule to one candidate. Returns the operation when it stands."""
    req = item.req
    provider, requester = item.provider, item.requester
    released = world.released.get(item.op_id)
    if isinstance(released, AddConnector):
        if released.name != item.name:
            req.error(
                codes.CFL_PENDING,
                f"The connection {provider.label} -> {requester.label} is released from the "
                f"pending queue under the connector name {released.name}; the row asks for "
                f"{item.name}.",
            )
            return None
        req.emit(released)
        req.info(
            codes.PEND_RELEASED,
            "This connector was on hold in the pending queue and is released by this run.",
        )
        return released
    if provider.port.port.direction not in (PortDirection.P, PortDirection.PR):
        swapped = requester.port.port.direction is PortDirection.P
        req.error(
            codes.CON_SIDE,
            f"Provider port {provider.label} is an {provider.port.port.direction.value} port; "
            "the provider side needs a P or PR port."
            + (" Provider and requester seem to be swapped." if swapped else ""),
            provider.port.port.path,
        )
    if requester.port.port.direction not in (PortDirection.R, PortDirection.PR):
        req.error(
            codes.CON_SIDE,
            f"Requester port {requester.label} is a {requester.port.port.direction.value} "
            "port; the requester side needs an R or PR port.",
            requester.port.port.path,
        )
    if provider.key == requester.key:
        req.error(
            codes.CON_SELF,
            f"Port {provider.label} cannot be connected to itself.",
            provider.port.port.path,
        )
    provider_interface = world.port_interface(req, provider.port, provider.component.name)
    requester_interface = world.port_interface(req, requester.port, requester.component.name)
    if req.rejected or provider_interface is None or requester_interface is None:
        return None
    why = incompatibility(provider_interface, requester_interface)
    if why:
        req.error(
            codes.CON_INCOMPATIBLE,
            f"{provider.label} and {requester.label} cannot be connected: {why}.",
        )
        return None
    existing = _existing_connector(world, item)
    if existing is not None:
        deleter = world.deleted_connectors.get(existing.path)
        if deleter is not None:
            for party, other in ((req, deleter), (deleter, req)):
                party.error(
                    codes.CFL_ADD_DELETE,
                    f"Connector {existing.name} ({provider.label} -> {requester.label}) is "
                    f"added in one row and deleted in another ({other.where}). Both rows are "
                    "rejected.",
                    existing.path,
                )
        elif item.explicit_name and item.name != existing.name:
            req.error(
                codes.CON_DUPLICATE,
                f"{provider.label} and {requester.label} are already connected by connector "
                f"{existing.name}. A second connector named {item.name} between the same two "
                "ports is not possible.",
                existing.path,
            )
        else:
            req.already = True
        return None
    _check_name(world, wiring, item)
    _check_multiplicity(world, wiring, item, requester_interface.kind)
    if req.rejected:
        return None
    missing = [side.port for side in (provider, requester) if not side.port.exists]
    hold = any(
        port.origin is PortOrigin.PENDING or port.route is not Route.APPLY for port in missing
    )
    op = AddConnector(
        id=item.op_id,
        **req.base(Route.HOLD if hold else Route.APPLY),
        composition_path=item.composition_path,
        name=item.name,
        provider_prototype=provider.prototype.name,
        provider_port=provider.port.port.name,
        requester_prototype=requester.prototype.name,
        requester_port=requester.port.port.name,
    )
    if not world.check_pending(req, op):
        return None
    req.emit(op, None, tuple(port.port.path for port in missing) if hold else ())
    wiring.add(item.composition_path, item.name, provider.key, requester.key)
    return op


# ---------------------------------------------------------------------- pending queue


def release_held(world: World, wiring: Wiring, held: Sequence[QueueItem]) -> None:
    """Release every held connector whose ports now exist and that is still valid."""
    model = world.model
    for item in held:
        entry = item.entry
        op = entry.operation
        if not isinstance(op, AddConnector):
            continue
        what = describe_operation(op)
        origin = f"requested {entry.created_utc or 'at an unknown time'} by " + (
            op.requester or "unknown requester"
        )
        sides: list[_Side] = []
        waiting = False
        gone = ""
        if op.composition_path not in model.compositions:
            gone = f"composition {op.composition_path}"
        for prototype_name, port_name in (
            (op.provider_prototype, op.provider_port),
            (op.requester_prototype, op.requester_port),
        ):
            prototype = world.prototype_in_composition.get((op.composition_path, prototype_name))
            component = model.components.get(prototype.type_path or "") if prototype else None
            if prototype is None or component is None:
                gone = gone or f"component {prototype_name} of {last_of(op.composition_path)}"
                continue
            port = model.find_port(component.path, port_name)
            if port is None:
                waiting = True
            else:
                sides.append(_Side(prototype, component, PortRef(port, PortOrigin.MODEL)))
        if gone:
            world.add_finding(
                codes.PEND_ORPHAN,
                f"Pending request ({what}, {origin}) cannot be checked: {gone} no longer "
                "exists in the repo. The entry is kept; remove it if it is obsolete.",
            )
            continue
        if waiting or len(sides) != 2:
            continue
        scratch = Req(
            index=-1,
            raw=RawRequestRow(
                sheet=_SHEET.value,
                row=RowRef(
                    file=op.source_file or "pending queue",
                    sheet=op.source_sheet or _SHEET.value,
                    row=op.source_row or 0,
                ),
                row_id=None,
                action=Action.ADD.value,
                requester=op.requester,
                comment=op.comment,
                values={},
            ),
            request_id=f"pending:{op.id}",
            sheet=_SHEET,
            action=Action.ADD,
        )
        candidate = _Candidate(scratch, op.composition_path, sides[0], sides[1], op.name, False)
        for side in sides:
            deleter = world.deleted_ports.get(side.port.port.path)
            if deleter is not None and not deleter.root().rejected:
                scratch.error(
                    codes.CFL_DELETED_PORT,
                    f"Port {side.label} is deleted by {deleter.where}.",
                )
        world.pending_by_id.pop(op.id, None)
        if not scratch.rejected and composition_editable(world, scratch, op.composition_path):
            _add(world, wiring, candidate)
        if scratch.rejected:
            world.pending_by_id.setdefault(op.id, entry)
            reasons = " ".join(
                finding.message
                for finding in scratch.findings
                if finding.severity is Severity.ERROR
            )
            world.add_finding(
                codes.PEND_RELEASE_FAILED,
                f"Pending request ({what}, {origin}): both ports exist now, but the connector "
                f"is not valid any more and stays on hold. {reasons}",
                f"{op.composition_path}/{op.name}",
            )
            continue
        item.keep = False
        if scratch.already:
            world.add_finding(
                codes.PEND_FULFILLED,
                f"Pending request ({what}, {origin}) is fulfilled by the repo and was closed.",
            )
            continue
        world.released[op.id] = op.model_copy(update={"route": Route.APPLY})
        world.add_finding(
            codes.PEND_RELEASED,
            f"Pending request ({what}, {origin}) is released: both ports exist now. The "
            "connector is part of this change set.",
            f"{op.composition_path}/{op.name}",
        )
        for finding in scratch.findings:
            if finding.severity is Severity.WARNING:
                world.add_finding(finding.code, f"Released {what}: {finding.message}")


# ---------------------------------------------------------------------- phase


def process_connectors(world: World, wiring: Wiring, reqs: Sequence[Req]) -> None:
    """Validate the ADD rows of the Connections sheet."""
    groups: dict[str, list[_Candidate]] = {}
    for req in reqs:
        candidate = _parse(world, req)
        if candidate is not None:
            groups.setdefault(candidate.op_id, []).append(candidate)
    for members in groups.values():
        first = members[0]
        label = f"The connection {first.provider.label} -> {first.requester.label}"
        if settle([(member.req, member.name) for member in members], label) is not None:
            _add(world, wiring, first)
