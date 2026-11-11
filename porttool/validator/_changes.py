"""MODIFY and DELETE rows: find the object of each row, RowID first and names second. Internal.

DELETE rows become operations here. MODIFY rows of the Ports sheet are only resolved here;
their new content is checked in the port phase, when the interfaces of the import are known.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from porttool.core.models import (
    AccessPoint,
    ArxmlOwner,
    Component,
    Connector,
    Port,
    Trigger,
)
from porttool.core.operations import (
    DeleteAccessPoint,
    DeleteConnector,
    DeletePort,
    DeleteTrigger,
    Route,
)
from porttool.core.rowid import row_id
from porttool.core.sheets import Action, Sheet
from porttool.validator import _ids, codes
from porttool.validator._common import header, list_rows, settle
from porttool.validator._state import Req
from porttool.validator._world import World, last_of
from porttool.validator.cells import (
    parse_access_kind,
    parse_init_cell,
    parse_number,
    parse_trigger_kind,
    token,
)

_PERIOD_TOLERANCE_S = 1e-9


@dataclass(slots=True)
class PortChange:
    """A MODIFY row of the Ports sheet whose port was found."""

    req: Req
    port: Port
    component: Component
    route: Route


def _reject_identity_change(req: Req, sheet: Sheet, expected: dict[str, tuple[str, ...]]) -> bool:
    """Reject when a filled identity cell names something else than the RowID's object."""
    differing = [
        f"'{header(sheet, key)}' (now '{req.cell(key)}', was '{allowed[0]}')"
        for key, allowed in expected.items()
        if req.cell(key) and req.cell(key) not in allowed
    ]
    if not differing:
        return False
    req.error(
        codes.ROW_IDENTITY_CHANGED,
        f"The RowID belongs to an existing object, but the row now differs in "
        f"{', '.join(differing)}. Renaming or moving an object is not supported in v1; "
        "delete the object and add a new one instead.",
    )
    return True


def _stale(req: Req, what: str) -> None:
    req.error(
        codes.ROW_STALE,
        f"The {what} of this row no longer exists in the repo: neither its RowID "
        f"({req.row_id}) nor its names match an object. It was renamed or removed after the "
        "workbook was exported. Export a fresh workbook and repeat the request.",
    )


def _matched_by_names(req: Req, what: str, path: str) -> None:
    req.warn(
        codes.ROW_STALE_ROWID,
        f"The RowID of this row is outdated (the {what} was moved since the export). The "
        "object was found by its names instead.",
        path,
    )


# ---------------------------------------------------------------------- target lookup


def _port_target(world: World, req: Req) -> Port | None:
    model = world.model
    swc_text = req.cell("swc")
    port_name = req.cell("port")
    component = world.unique_swc(swc_text) if swc_text else None
    named = (
        model.find_port(component.path, port_name) if component is not None and port_name else None
    )
    if named is not None and row_id(Sheet.PORTS, named.path) == req.row_id:
        return named
    by_id = world.ports_by_rowid.get(req.row_id or "")
    if by_id is not None:
        owner = model.components.get(by_id.swc_path)
        expected = {
            "swc": (owner.name if owner is not None else last_of(by_id.swc_path), by_id.swc_path),
            "port": (by_id.name,),
        }
        return None if _reject_identity_change(req, Sheet.PORTS, expected) else by_id
    if named is not None:
        _matched_by_names(req, "port", named.path)
        return named
    _stale(req, "port")
    return None


def _connector_cells(world: World, connector: Connector) -> dict[str, tuple[str, ...]]:
    model = world.model
    expected: dict[str, tuple[str, ...]] = {
        "connector_name": (connector.name,),
        "composition": (last_of(connector.composition_path), connector.composition_path),
    }
    for side, end in (("provider", connector.provider), ("requester", connector.requester)):
        prototype = model.prototypes.get(end.prototype_path or "")
        port = model.ports.get(end.port_path or "")
        if prototype is not None:
            expected[f"{side}_swc"] = (prototype.name,)
        if port is not None:
            expected[f"{side}_port"] = (port.name,)
    return expected


def _connector_target(world: World, req: Req) -> Connector | None:
    model = world.model
    composition_text = req.cell("composition")
    name = req.cell("connector_name")
    composition = world.unique_composition(composition_text) if composition_text else None
    named: dict[str, Connector] = {}
    if composition is not None and name:
        by_name = model.connectors.get(f"{composition.path}/{name}")
        if by_name is not None:
            named[by_name.path] = by_name
    provider = req.cell("provider_swc")
    provider_port = req.cell("provider_port")
    requester = req.cell("requester_swc")
    requester_port = req.cell("requester_port")
    if provider and provider_port and requester and requester_port:
        if composition is not None:
            in_composition = world.prototype_in_composition.get((composition.path, provider))
            prototypes = [in_composition] if in_composition is not None else []
        else:
            prototypes = world.prototypes_by_name.get(provider, [])
        for prototype in prototypes:
            port = model.find_port(prototype.type_path or "", provider_port)
            if port is None:
                continue
            for connector in model.connectors_at(prototype.path, port.path):
                if (
                    connector.provider.prototype_path != prototype.path
                    or connector.provider.port_path != port.path
                ):
                    continue
                other = model.prototypes.get(connector.requester.prototype_path or "")
                other_port = model.ports.get(connector.requester.port_path or "")
                if (
                    other is not None
                    and other_port is not None
                    and other.name == requester
                    and other_port.name == requester_port
                ):
                    named[connector.path] = connector
    for connector in named.values():
        if row_id(Sheet.CONNECTIONS, connector.path) == req.row_id:
            return connector
    by_id = world.connectors_by_rowid.get(req.row_id or "")
    if by_id is not None:
        expected = _connector_cells(world, by_id)
        return None if _reject_identity_change(req, Sheet.CONNECTIONS, expected) else by_id
    if len(named) == 1:
        found = next(iter(named.values()))
        _matched_by_names(req, "connector", found.path)
        return found
    _stale(req, "connector")
    return None


def _access_target(world: World, req: Req) -> AccessPoint | None:
    model = world.model
    swc_text = req.cell("swc")
    runnable_name = req.cell("runnable")
    port_name = req.cell("port")
    target = req.cell("target")
    kind_text = req.cell("access_kind")
    kind = parse_access_kind(kind_text) if kind_text else None
    component = world.unique_swc(swc_text) if swc_text else None
    named: list[AccessPoint] = []
    if component is not None and runnable_name:
        runnable = model.find_runnable(component.path, runnable_name)
        for access in model.access_points_of_runnable(runnable.path) if runnable else ():
            port = model.ports.get(access.port_path or "")
            if port_name and (port is None or port.name != port_name):
                continue
            if target and access.target != target:
                continue
            if kind_text and access.kind is not kind:
                continue
            named.append(access)
    for access in named:
        if row_id(Sheet.RUNNABLE_ACCESS, access.path) == req.row_id:
            return access
    by_id = world.access_by_rowid.get(req.row_id or "")
    if by_id is not None:
        owner = model.components.get(by_id.swc_path)
        runnable = model.runnables.get(by_id.runnable_path)
        port = model.ports.get(by_id.port_path or "")
        expected: dict[str, tuple[str, ...]] = {
            "swc": (owner.name if owner is not None else last_of(by_id.swc_path), by_id.swc_path),
            "runnable": (runnable.name if runnable else last_of(by_id.runnable_path),),
        }
        if port is not None:
            expected["port"] = (port.name,)
        if by_id.target is not None:
            expected["target"] = (by_id.target,)
        if kind_text and kind is not by_id.kind:
            expected["access_kind"] = (by_id.kind.value,)
        return None if _reject_identity_change(req, Sheet.RUNNABLE_ACCESS, expected) else by_id
    if len(named) == 1:
        _matched_by_names(req, "access point", named[0].path)
        return named[0]
    _stale(req, "access point")
    return None


def _trigger_target(world: World, req: Req) -> Trigger | None:
    model = world.model
    swc_text = req.cell("swc")
    runnable_name = req.cell("runnable")
    port_name = req.cell("port")
    target = req.cell("target")
    kind_text = req.cell("trigger_kind")
    kind = parse_trigger_kind(kind_text) if kind_text else None
    period_text = req.cell("period_ms")
    period_ms = parse_number(period_text) if period_text else None
    component = world.unique_swc(swc_text) if swc_text else None
    named: list[Trigger] = []
    if component is not None and runnable_name:
        runnable = model.find_runnable(component.path, runnable_name)
        for trigger in model.triggers_of(runnable.path) if runnable else ():
            port = model.ports.get(trigger.port_path or "")
            if port_name and (port is None or port.name != port_name):
                continue
            if target and trigger.target != target:
                continue
            if kind_text and trigger.kind is not kind:
                continue
            if period_ms is not None and (
                trigger.period_s is None
                or abs(trigger.period_s - period_ms / 1000.0) > _PERIOD_TOLERANCE_S
            ):
                continue
            named.append(trigger)
    for trigger in named:
        if row_id(Sheet.RUNNABLE_TRIGGERS, trigger.path) == req.row_id:
            return trigger
    by_id = world.triggers_by_rowid.get(req.row_id or "")
    if by_id is not None:
        owner = model.components.get(by_id.swc_path)
        runnable = model.runnables.get(by_id.runnable_path or "")
        port = model.ports.get(by_id.port_path or "")
        expected: dict[str, tuple[str, ...]] = {
            "swc": (owner.name if owner is not None else last_of(by_id.swc_path), by_id.swc_path),
        }
        if runnable is not None:
            expected["runnable"] = (runnable.name,)
        if port is not None:
            expected["port"] = (port.name,)
        if by_id.target is not None:
            expected["target"] = (by_id.target,)
        if kind_text and kind is not by_id.kind:
            expected["trigger_kind"] = (by_id.kind.value,)
        return None if _reject_identity_change(req, Sheet.RUNNABLE_TRIGGERS, expected) else by_id
    if len(named) == 1:
        _matched_by_names(req, "trigger", named[0].path)
        return named[0]
    _stale(req, "trigger")
    return None


# ---------------------------------------------------------------------- per sheet


def _owner(world: World, req: Req, swc_path: str) -> tuple[Component, Route] | None:
    component = world.model.components.get(swc_path)
    if component is None:
        req.error(
            codes.REF_UNKNOWN_SWC,
            f"SWC '{swc_path}' of this row is not loaded, so the request cannot be checked.",
            swc_path,
        )
        return None
    route = world.route_for(req, component)
    return (component, route) if route is not None else None


def composition_editable(world: World, req: Req, composition_path: str) -> bool:
    """False (with a Finding) when connectors of this composition must not be edited."""
    name = last_of(composition_path)
    component = world.model.components.get(composition_path)
    owners = {world.config.owner_of(name)}
    if component is not None:
        owners.add(component.owner)
    blocked = owners & {ArxmlOwner.LOCKED, ArxmlOwner.MODEL}
    if not blocked:
        return True
    reason = "locked" if ArxmlOwner.LOCKED in blocked else "owned by a model"
    req.error(
        codes.OWN_COMPOSITION_LOCKED,
        f"Composition {name} is {reason}; PortTool does not edit its connectors.",
        composition_path,
    )
    return False


def _modify_signature(req: Req) -> tuple[str, str, object]:
    single, pairs, _ = parse_init_cell(req.cell("init_value"))
    return (token(req.cell("direction")), req.cell("interface"), (single, pairs))


def _ports(world: World, reqs: Sequence[Req]) -> list[PortChange]:
    deletes: dict[str, list[Req]] = {}
    modifies: dict[str, list[Req]] = {}
    found: dict[str, tuple[Port, Component, Route]] = {}
    for req in reqs:
        port = _port_target(world, req)
        if port is None:
            continue
        owner = _owner(world, req, port.swc_path)
        if owner is None:
            continue
        found[port.path] = (port, owner[0], owner[1])
        bucket = deletes if req.action is Action.DELETE else modifies
        bucket.setdefault(port.path, []).append(req)

    changes: list[PortChange] = []
    for path, (port, component, route) in found.items():
        deleting = deletes.get(path, [])
        modifying = modifies.get(path, [])
        label = f"Port {component.name}.{port.name}"
        if deleting and modifying:
            for req in (*deleting, *modifying):
                req.error(
                    codes.CFL_ADD_DELETE,
                    f"{label} is changed in {list_rows(modifying)} and deleted in "
                    f"{list_rows(deleting)}. All of these rows are rejected.",
                    path,
                )
            continue
        if deleting:
            leader = deleting[0]
            for req in deleting[1:]:
                req.merge_into(leader)
            op = DeletePort(
                id=_ids.delete_port(path),
                **leader.base(route),
                swc_path=port.swc_path,
                name=port.name,
            )
            if world.check_pending(leader, op):
                leader.emit(op, world.team_of(component) if route is Route.MODEL else None)
                world.deleted_ports[path] = leader
        if modifying:
            settled = settle([(req, _modify_signature(req)) for req in modifying], label)
            if settled is not None:
                world.modified_ports[path] = (settled, None)
                changes.append(PortChange(settled, port, component, route))
    return changes


def _connectors(world: World, reqs: Sequence[Req]) -> None:
    groups: dict[str, tuple[Connector, list[Req]]] = {}
    for req in reqs:
        connector = _connector_target(world, req)
        if connector is None or not composition_editable(world, req, connector.composition_path):
            continue
        groups.setdefault(connector.path, (connector, []))[1].append(req)
    for path, (connector, rows) in groups.items():
        leader = rows[0]
        for req in rows[1:]:
            req.merge_into(leader)
        leader.emit(
            DeleteConnector(
                id=_ids.delete_connector(path),
                **leader.base(Route.APPLY),
                composition_path=connector.composition_path,
                name=connector.name,
            )
        )
        world.deleted_connectors[path] = leader


def _access_points(world: World, reqs: Sequence[Req]) -> None:
    model = world.model
    groups: dict[str, tuple[AccessPoint, Component, Route, list[Req]]] = {}
    for req in reqs:
        access = _access_target(world, req)
        if access is None:
            continue
        owner = _owner(world, req, access.swc_path)
        if owner is None:
            continue
        groups.setdefault(access.path, (access, owner[0], owner[1], []))[3].append(req)
    for path, (access, component, route, rows) in groups.items():
        leader = rows[0]
        for req in rows[1:]:
            req.merge_into(leader)
        port = model.ports.get(access.port_path or "")
        runnable = model.runnables.get(access.runnable_path)
        if port is None or access.target is None:
            leader.error(
                codes.ROW_UNSUPPORTED,
                "This access point has no port or no target in the repo. Deleting it is not "
                "supported in v1; use DaVinci Developer.",
                path,
            )
            continue
        runnable_name = runnable.name if runnable is not None else last_of(access.runnable_path)
        op = DeleteAccessPoint(
            id=_ids.access_point(
                "delete_access_point",
                access.swc_path,
                runnable_name,
                port.name,
                access.target,
                access.kind,
            ),
            **leader.base(route),
            swc_path=access.swc_path,
            runnable_name=runnable_name,
            port_name=port.name,
            target=access.target,
            kind=access.kind,
        )
        if world.check_pending(leader, op):
            leader.emit(op, world.team_of(component) if route is Route.MODEL else None)
            world.deleted_access[path] = leader


def _triggers(world: World, reqs: Sequence[Req]) -> None:
    groups: dict[str, tuple[Trigger, Component, Route, list[Req]]] = {}
    for req in reqs:
        trigger = _trigger_target(world, req)
        if trigger is None:
            continue
        owner = _owner(world, req, trigger.swc_path)
        if owner is None:
            continue
        groups.setdefault(trigger.path, (trigger, owner[0], owner[1], []))[3].append(req)
    for path, (trigger, component, route, rows) in groups.items():
        leader = rows[0]
        for req in rows[1:]:
            req.merge_into(leader)
        op = DeleteTrigger(
            id=_ids.delete_trigger(trigger.swc_path, trigger.name),
            **leader.base(route),
            swc_path=trigger.swc_path,
            name=trigger.name,
        )
        if world.check_pending(leader, op):
            leader.emit(op, world.team_of(component) if route is Route.MODEL else None)
            world.deleted_triggers[path] = leader


def _check_deleted_ports_unused(world: World) -> None:
    """A port can only go when every connector, access point and trigger on it goes too."""
    model = world.model
    for path, req in world.deleted_ports.items():
        if req.rejected:
            continue
        port = model.ports[path]
        blockers: list[str] = []
        users: list[tuple[str, Req | None]] = []
        for prototype in model.prototypes_of_type(port.swc_path):
            for connector in model.connectors_at(prototype.path, path):
                users.append(
                    (f"connector {connector.name}", world.deleted_connectors.get(connector.path))
                )
        for access in model.access_points_of_port(path):
            runnable = model.runnables.get(access.runnable_path)
            label = f"{access.kind.value} of runnable {runnable.name if runnable else '?'}"
            users.append((label, world.deleted_access.get(access.path)))
        for trigger in world.triggers_on_port(port.swc_path, path):
            users.append((f"trigger {trigger.name}", world.deleted_triggers.get(trigger.path)))
        for label, deleter in users:
            if deleter is None or deleter.root().rejected:
                blockers.append(label)
            else:
                req.depends.append(deleter)
        if blockers:
            listed = ", ".join(blockers[:5]) + (
                f" and {len(blockers) - 5} more" if len(blockers) > 5 else ""
            )
            req.error(
                codes.PORT_IN_USE,
                f"Port {port.name} is still used by {listed}. Add DELETE rows for them in the "
                "same import, then the port can be deleted.",
                path,
            )


def process_changes(world: World, reqs: Sequence[Req]) -> list[PortChange]:
    """Resolve every MODIFY and DELETE row. Returns the MODIFY rows of the Ports sheet."""
    by_sheet: dict[Sheet, list[Req]] = {}
    for req in reqs:
        if req.rejected or req.sheet is None or req.action in (None, Action.ADD):
            continue
        by_sheet.setdefault(req.sheet, []).append(req)
    # Connectors, access points and triggers first: deleting a port needs them resolved.
    _connectors(world, by_sheet.get(Sheet.CONNECTIONS, []))
    _access_points(world, by_sheet.get(Sheet.RUNNABLE_ACCESS, []))
    _triggers(world, by_sheet.get(Sheet.RUNNABLE_TRIGGERS, []))
    changes = _ports(world, by_sheet.get(Sheet.PORTS, []))
    _check_deleted_ports_unused(world)
    return changes
