"""Ports sheet: ADD rows become ``AddPort``, MODIFY rows become ``ModifyPort``. Internal."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from porttool.core.models import (
    Component,
    ComSpec,
    InterfaceKind,
    Port,
    PortDirection,
    PortInterface,
)
from porttool.core.operations import AddPort, InitValueSpec, ModifyPort, Route
from porttool.core.sheets import Sheet
from porttool.validator import _ids, codes
from porttool.validator._changes import PortChange
from porttool.validator._common import header, reject_case_clashes, require, settle
from porttool.validator._compat import incompatibility
from porttool.validator._state import Req
from porttool.validator._typeinfo import init_value_problem, parse_exact
from porttool.validator._world import SR_STYLE, PortOrigin, PortRef, World, scope_clash
from porttool.validator.cells import is_none_literal, parse_init_cell, parse_port_direction
from porttool.validator.names import NameKind

_SHEET = Sheet.PORTS


@dataclass(slots=True)
class ServerPort:
    """A new client-server P-port of this import: each of its operations needs a runnable."""

    req: Req
    component: Component
    route: Route
    name: str
    interface: PortInterface


@dataclass(slots=True)
class _Candidate:
    req: Req
    component: Component
    route: Route
    name: str
    direction: PortDirection
    interface: PortInterface


# ---------------------------------------------------------------------- init values


def init_values(
    world: World, req: Req, interface: PortInterface, text: str, *, required: bool
) -> tuple[InitValueSpec, ...] | None:
    """Read the init value cell for a port of ``interface``. None when the cell is rejected."""
    single, pairs, reason = parse_init_cell(text)
    if reason:
        req.error(codes.PORT_INIT_FORMAT, f"Init value '{text}' cannot be read: {reason}.")
        return None
    if interface.kind not in SR_STYLE:
        if single is not None or pairs is not None:
            req.error(
                codes.PORT_INIT_NOT_USED,
                f"An init value is given, but {interface.name} is a {interface.kind.value} "
                "interface. Only ports with data elements have init values. Clear the cell.",
            )
            return None
        return ()
    errors = 0
    values: dict[str, str] = {}
    elements = interface.elements
    if single is not None:
        if is_none_literal(single):
            values = {element.name: single for element in elements if not element.is_queued}
        elif len(elements) != 1:
            req.error(
                codes.PORT_INIT_FORMAT,
                f"Interface {interface.name} has {len(elements)} data elements, so one bare "
                "init value is not enough. Use the form Element=value;Element2=value2.",
            )
            return None
        elif elements[0].is_queued:
            req.error(
                codes.PORT_INIT_NOT_USED,
                f"Element {elements[0].name} of interface {interface.name} is queued; a queued "
                "element has no init value. Clear the cell.",
            )
            return None
        else:
            values = {elements[0].name: single}
    for name, value in pairs or ():
        element = interface.element(name)
        if element is None:
            req.error(
                codes.PORT_INIT_FORMAT,
                f"Init value names element '{name}', but interface {interface.name} has no "
                "such data element.",
            )
            errors += 1
        elif name in values:
            req.error(codes.PORT_INIT_FORMAT, f"Init value names element {name} twice.")
            errors += 1
        elif element.is_queued:
            req.error(
                codes.PORT_INIT_NOT_USED,
                f"Element {name} of interface {interface.name} is queued; a queued element "
                "has no init value.",
            )
            errors += 1
        else:
            values[name] = value
    specs: list[InitValueSpec] = []
    for element in elements:
        if element.is_queued:
            continue
        given = values.get(element.name)
        if given is None:
            if required and interface.kind is InterfaceKind.SENDER_RECEIVER:
                req.error(
                    codes.PORT_INIT_MISSING,
                    f"Data element {element.name} of interface {interface.name} has no init "
                    "value. Enter a value, or the word none to leave it without one on purpose.",
                )
                errors += 1
            continue
        if is_none_literal(given):
            specs.append(InitValueSpec(element=element.name, value=None))
            continue
        code, why = init_value_problem(world.type_info(element.type_path), given)
        if code:
            req.error(code, f"Init value '{given}' of data element {element.name}: {why}.")
            errors += 1
        else:
            specs.append(InitValueSpec(element=element.name, value=given))
    return None if errors else tuple(specs)


def same_value(left: str | None, right: str | None) -> bool:
    if left is None or right is None:
        return left is None and right is None
    first, second = parse_exact(left), parse_exact(right)
    if first is not None and second is not None:
        return first == second
    return left.strip() == right.strip()


def _requested_init(interface: PortInterface, text: str) -> dict[str, str | None]:
    """Init values a cell asks for, element -> value, without judging them. Empty if unclear."""
    single, pairs, reason = parse_init_cell(text)
    if reason:
        return {}
    plain = [element.name for element in interface.elements if not element.is_queued]
    if single is not None:
        if is_none_literal(single):
            return dict.fromkeys(plain)
        return {plain[0]: single} if len(plain) == 1 else {}
    return {name: None if is_none_literal(value) else value for name, value in pairs or ()}


def _init_matches(specs: Sequence[InitValueSpec], port: Port) -> bool:
    """True when the port already has every requested init value."""
    current = {spec.target: spec.init_value for spec in port.com_specs}
    return all(same_value(spec.value, current.get(spec.element)) for spec in specs)


# ---------------------------------------------------------------------- ADD


def _parse(world: World, req: Req) -> _Candidate | None:
    if not require(req, _SHEET, "swc", "port", "direction", "interface"):
        return None
    name = req.cell("port")
    component = world.resolve_swc(req, req.cell("swc"))
    req.problems(world.names.check(name, NameKind.PORT))
    direction = parse_port_direction(req.cell("direction"))
    if direction is None:
        req.error(
            codes.PORT_DIRECTION,
            f"Direction '{req.cell('direction')}' of port {name} is not P, R or PR.",
        )
    interface = world.resolve_interface(req, req.cell("interface"))
    route = world.route_for(req, component) if component is not None else None
    if component is None or direction is None or interface is None or route is None:
        return None
    if req.rejected:
        return None
    return _Candidate(req, component, route, name, direction, interface)


def _add_signature(candidate: _Candidate) -> tuple[object, ...]:
    single, pairs, _ = parse_init_cell(candidate.req.cell("init_value"))
    return (candidate.direction, candidate.interface.path, single, pairs)


def _check_pr(req: Req, name: str, direction: PortDirection, interface: PortInterface) -> None:
    if direction is PortDirection.PR and interface.kind not in SR_STYLE:
        req.error(
            codes.PORT_PR_KIND,
            f"Port {name} cannot be a PR port: {interface.name} is a {interface.kind.value} "
            "interface. PR ports need a sender-receiver style interface.",
        )


def _add(world: World, candidate: _Candidate) -> ServerPort | None:
    req = candidate.req
    component = candidate.component
    name = candidate.name
    path = f"{component.path}/{name}"
    interface = candidate.interface
    existing = world.model.find_port(component.path, name)
    if existing is not None:
        deleter = world.deleted_ports.get(existing.path)
        if deleter is not None:
            for party, counterpart in ((req, deleter), (deleter, req)):
                party.error(
                    codes.CFL_ADD_DELETE,
                    f"Port {component.name}.{name} is added in one row and deleted in another "
                    f"({counterpart.where}). Both rows are rejected; use one MODIFY row to "
                    "change a port.",
                    path,
                )
            return None
        if existing.direction is candidate.direction and existing.interface_path == interface.path:
            req.already = True
            wanted = _requested_init(interface, req.cell("init_value"))
            current = {spec.target: spec.init_value for spec in existing.com_specs}
            if any(not same_value(value, current.get(key)) for key, value in wanted.items()):
                req.warn(
                    codes.PORT_INIT_DIFFERS,
                    f"Port {component.name}.{name} already exists with this direction and "
                    "interface, but the init value of the row is not the one in the repo. "
                    "Nothing is changed; use MODIFY to change the init value.",
                    path,
                )
            return None
        req.error(
            codes.NAME_DUPLICATE,
            f"SWC {component.name} already has a port {name} "
            f"({existing.direction.value}, {existing.interface_path or 'no interface'}). Use "
            "MODIFY to change it, or choose another name.",
            path,
        )
        return None
    clash = scope_clash(world.port_scope(component.path), name)
    if clash is not None:
        code, other_name = clash
        req.error(
            code,
            f"Port name '{name}' is not free in SWC {component.name}: "
            + (
                "the internal behavior has this name."
                if code == codes.NAME_DUPLICATE
                else f"'{other_name}' differs only by letter case."
            ),
            path,
        )
    _check_pr(req, name, candidate.direction, interface)
    specs = init_values(world, req, interface, req.cell("init_value"), required=True)
    if req.rejected or specs is None:
        return None
    op = AddPort(
        id=_ids.add_port(component.path, name),
        **req.base(candidate.route),
        swc_path=component.path,
        name=name,
        direction=candidate.direction,
        interface_path=interface.path,
        init_values=specs,
    )
    if not world.check_pending(req, op):
        return None
    needs = (interface.path,) if interface.path not in world.model.interfaces else ()
    is_model = candidate.route is Route.MODEL
    req.emit(op, world.team_of(component) if is_model else None, needs if is_model else ())
    world.new_ports[(component.path, name)] = PortRef(
        port=Port(
            path=path,
            name=name,
            swc_path=component.path,
            direction=candidate.direction,
            interface_path=interface.path,
            com_specs=tuple(ComSpec(target=s.element, init_value=s.value) for s in specs),
        ),
        origin=PortOrigin.NEW,
        route=candidate.route,
        req=req,
    )
    if interface.kind is InterfaceKind.CLIENT_SERVER and candidate.direction is PortDirection.P:
        return ServerPort(req, component, candidate.route, name, interface)
    return None


# ---------------------------------------------------------------------- MODIFY


def _users_broken_by(world: World, port: Port, new_interface: PortInterface) -> list[str]:
    """Existing connectors, access points and triggers that the new interface would break."""
    model = world.model
    broken: list[str] = []
    for prototype in model.prototypes_of_type(port.swc_path):
        for connector in model.connectors_at(prototype.path, port.path):
            deleter = world.deleted_connectors.get(connector.path)
            if deleter is not None and not deleter.root().rejected:
                continue
            is_provider = (
                connector.provider.prototype_path == prototype.path
                and connector.provider.port_path == port.path
            )
            other_end = connector.requester if is_provider else connector.provider
            other = model.ports.get(other_end.port_path or "")
            other_interface = world.interface(other.interface_path) if other else None
            if other_interface is None:
                broken.append(f"connector {connector.name} (its other port cannot be checked)")
                continue
            why = (
                incompatibility(new_interface, other_interface)
                if is_provider
                else incompatibility(other_interface, new_interface)
            )
            if why:
                broken.append(f"connector {connector.name} ({why})")
    targets = {element.name for element in new_interface.elements} | {
        operation.name for operation in new_interface.operations
    }
    for access in model.access_points_of_port(port.path):
        deleter = world.deleted_access.get(access.path)
        if (deleter is None or deleter.root().rejected) and access.target not in targets:
            broken.append(f"{access.kind.value} of '{access.target}'")
    for trigger in world.triggers_on_port(port.swc_path, port.path):
        deleter = world.deleted_triggers.get(trigger.path)
        if (deleter is None or deleter.root().rejected) and trigger.target not in targets:
            broken.append(f"trigger {trigger.name} on '{trigger.target}'")
    return broken


def _modify(world: World, change: PortChange) -> None:
    req = change.req
    port = change.port
    label = f"{change.component.name}.{port.name}"
    direction_text = req.cell("direction")
    if direction_text and parse_port_direction(direction_text) is not port.direction:
        req.error(
            codes.ROW_IDENTITY_CHANGED,
            f"Port {label} has direction {port.direction.value}; the row says "
            f"'{direction_text}' in cell '{header(_SHEET, 'direction')}'. Changing the "
            "direction is not supported in v1; delete the port and add a new one instead.",
            port.path,
        )
    interface = world.model.interfaces.get(port.interface_path or "")
    new_path: str | None = None
    interface_text = req.cell("interface")
    if interface_text:
        resolved = world.resolve_interface(req, interface_text)
        if resolved is None:
            return
        if resolved.path != port.interface_path:
            new_path = resolved.path
            interface = resolved
    if interface is None:
        req.error(
            codes.REF_PORT_WITHOUT_INTERFACE,
            f"Port {label} refers to interface '{port.interface_path or '(none)'}', which is "
            "not loaded. The request cannot be checked.",
            port.path,
        )
        return
    if new_path is not None:
        _check_pr(req, port.name, port.direction, interface)
        broken = _users_broken_by(world, port, interface)
        if broken:
            listed = "; ".join(broken[:5]) + (
                f"; and {len(broken) - 5} more" if len(broken) > 5 else ""
            )
            req.error(
                codes.PORT_MODIFY_BREAKS,
                f"Port {label} cannot get interface {interface.name}: it does not fit "
                f"{listed}. Delete or change these first.",
                port.path,
            )
    init_text = req.cell("init_value")
    specs: tuple[InitValueSpec, ...] | None = ()
    if init_text or new_path is not None:
        specs = init_values(world, req, interface, init_text, required=new_path is not None)
    if req.rejected or specs is None:
        return
    if new_path is None and _init_matches(specs, port):
        req.already = True
        return
    op = ModifyPort(
        id=_ids.modify_port(port.path),
        **req.base(change.route),
        swc_path=port.swc_path,
        name=port.name,
        interface_path=new_path,
        init_values=specs,
    )
    if not world.check_pending(req, op):
        return
    is_model = change.route is Route.MODEL
    needs = (new_path,) if new_path is not None and new_path not in world.model.interfaces else ()
    req.emit(op, world.team_of(change.component) if is_model else None, needs if is_model else ())
    world.modified_ports[port.path] = (req, new_path)


# ---------------------------------------------------------------------- phase


def process_ports(
    world: World, adds: Sequence[Req], changes: Sequence[PortChange]
) -> list[ServerPort]:
    """Validate the ADD and MODIFY rows of the Ports sheet. Returns the new server ports."""
    groups: dict[tuple[str, str], list[_Candidate]] = {}
    for req in adds:
        candidate = _parse(world, req)
        if candidate is not None:
            groups.setdefault((candidate.component.path, candidate.name), []).append(candidate)

    leaders: list[_Candidate] = []
    names_per_swc: dict[str, dict[str, list[Req]]] = {}
    for members in groups.values():
        first = members[0]
        label = f"Port {first.component.name}.{first.name}"
        if settle([(member.req, _add_signature(member)) for member in members], label) is None:
            continue
        leaders.append(first)
        names_per_swc.setdefault(first.component.path, {})[first.name] = [first.req]
    for names in names_per_swc.values():
        reject_case_clashes(names, "Port name")

    for change in changes:
        if not change.req.rejected:
            _modify(world, change)

    servers: list[ServerPort] = []
    for candidate in leaders:
        if candidate.req.rejected:
            continue
        server = _add(world, candidate)
        if server is not None:
            servers.append(server)
    return servers
