"""RunnableTriggers and RunnableAccess sheets, and the runnables of new server ports. Internal."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from porttool.core.models import (
    AccessKind,
    Component,
    InterfaceKind,
    PortDirection,
    PortInterface,
    TaskMapping,
    Trigger,
    TriggerKind,
)
from porttool.core.operations import AddAccessPoint, AddServerRunnable, AddTrigger, Route
from porttool.core.sheets import Sheet
from porttool.validator import _ids, codes
from porttool.validator._common import header, require, settle
from porttool.validator._ports import ServerPort
from porttool.validator._state import Req
from porttool.validator._world import SR_STYLE, PortOrigin, PortRef, World, last_of, scope_clash
from porttool.validator.cells import parse_access_kind, parse_number, parse_trigger_kind
from porttool.validator.names import NameKind

_TRIGGERS = Sheet.RUNNABLE_TRIGGERS
_ACCESS = Sheet.RUNNABLE_ACCESS
_PERIOD_TOLERANCE_S = 1e-9

_PORTLESS = (TriggerKind.PERIODIC, TriggerKind.INIT, TriggerKind.BACKGROUND)
_PORT_BOUND = (
    TriggerKind.DATA_RECEIVED,
    TriggerKind.DATA_RECEIVE_ERROR,
    TriggerKind.OPERATION_INVOKED,
    TriggerKind.MODE_SWITCH,
)

#: Access kinds that v1 can create, by what they do to the target.
_ACCESS_CLASS: dict[AccessKind, str] = {
    AccessKind.IMPLICIT_READ: "read",
    AccessKind.EXPLICIT_READ: "read",
    AccessKind.QUEUED_RECEIVE: "read",
    AccessKind.IMPLICIT_WRITE: "write",
    AccessKind.EXPLICIT_SEND: "write",
    AccessKind.SYNC_SERVER_CALL: "call",
    AccessKind.ASYNC_SERVER_CALL: "call",
    AccessKind.MODE_ACCESS: "mode access",
    AccessKind.MODE_SWITCH: "mode switch",
}

ClaimKey = tuple[str, str, str]  # (SWC path, port name, operation)


@dataclass(slots=True)
class _TriggerItem:
    req: Req
    component: Component
    route: Route
    runnable: str
    kind: TriggerKind
    port: PortRef | None = None
    target: str | None = None
    period_s: float | None = None
    task: str | None = None

    @property
    def port_name(self) -> str | None:
        return self.port.port.name if self.port is not None else None


@dataclass(slots=True)
class _AccessItem:
    req: Req
    component: Component
    route: Route
    runnable: str
    port: PortRef
    target: str
    kind: AccessKind


# ---------------------------------------------------------------------- triggers: reading


def _trigger_fit(kind: TriggerKind, direction: PortDirection, interface: PortInterface) -> str:
    receiving = direction in (PortDirection.R, PortDirection.PR)
    if kind in (TriggerKind.DATA_RECEIVED, TriggerKind.DATA_RECEIVE_ERROR):
        if interface.kind is not InterfaceKind.SENDER_RECEIVER or not receiving:
            return "needs an R or PR port with a sender-receiver interface"
    elif kind is TriggerKind.OPERATION_INVOKED:
        if interface.kind is not InterfaceKind.CLIENT_SERVER or direction is not PortDirection.P:
            return "needs a P port with a client-server interface (a server port)"
    elif interface.kind is not InterfaceKind.MODE_SWITCH or not receiving:
        return "needs an R port with a mode-switch interface"
    return ""


def _trigger_target_known(interface: PortInterface, kind: TriggerKind, target: str) -> bool:
    if kind is TriggerKind.OPERATION_INVOKED:
        return interface.operation(target) is not None
    if kind is TriggerKind.MODE_SWITCH:
        return True  # mode names are not part of the model; DVWspChecker judges them
    return interface.element(target) is not None


def _parse_trigger(
    world: World, req: Req, claims: dict[ClaimKey, list[Req]]
) -> _TriggerItem | None:
    if not require(req, _TRIGGERS, "swc", "runnable", "trigger_kind"):
        return None
    component = world.resolve_swc(req, req.cell("swc"))
    kind = parse_trigger_kind(req.cell("trigger_kind"))
    if kind is None or kind not in (*_PORTLESS, *_PORT_BOUND):
        req.error(
            codes.TRG_KIND,
            f"Trigger kind '{req.cell('trigger_kind')}' is not supported in v1. Supported: "
            + ", ".join(k.value for k in (*_PORTLESS, *_PORT_BOUND))
            + ".",
        )
        kind = None
    route = world.route_for(req, component) if component is not None else None
    if component is None or kind is None or route is None:
        return None
    item = _TriggerItem(req, component, route, req.cell("runnable"), kind)
    period_text = req.cell("period_ms")
    if kind in _PORTLESS:
        for key in ("port", "target"):
            if req.cell(key):
                req.error(
                    codes.TRG_CELL_NOT_USED,
                    f"Cell '{header(_TRIGGERS, key)}' is filled, but a trigger of kind "
                    f"'{kind.value}' has no port. Clear the cell.",
                )
        if kind is TriggerKind.PERIODIC:
            period = parse_number(period_text) if period_text else None
            if period is None or period <= 0:
                req.error(
                    codes.TRG_PERIOD,
                    f"A periodic trigger needs a period in milliseconds greater than 0; cell "
                    f"'{header(_TRIGGERS, 'period_ms')}' contains '{period_text}'.",
                )
            else:
                item.period_s = period / 1000.0
    if period_text and kind is not TriggerKind.PERIODIC:
        req.error(
            codes.TRG_CELL_NOT_USED,
            f"Cell '{header(_TRIGGERS, 'period_ms')}' is filled, but only periodic triggers "
            "have a period. Clear the cell.",
        )
    if kind in _PORT_BOUND and require(req, _TRIGGERS, "port", "target"):
        port_name = req.cell("port")
        target = req.cell("target")
        if kind is TriggerKind.OPERATION_INVOKED:
            claims.setdefault((component.path, port_name, target), []).append(req)
        ref = world.resolve_port(req, component, port_name)
        interface = world.port_interface(req, ref, component.name) if ref is not None else None
        if ref is not None and interface is not None:
            why = _trigger_fit(kind, ref.port.direction, interface)
            if why:
                req.error(
                    codes.TRG_FIT,
                    f"A trigger of kind '{kind.value}' {why}; port {component.name}."
                    f"{port_name} is a {ref.port.direction.value} port with the "
                    f"{interface.kind.value} interface {interface.name}.",
                    ref.port.path,
                )
            elif not _trigger_target_known(interface, kind, target):
                req.error(
                    codes.REF_UNKNOWN_TARGET,
                    f"Interface {interface.name} of port {component.name}.{port_name} has no "
                    f"{'operation' if kind is TriggerKind.OPERATION_INVOKED else 'data element'}"
                    f" '{target}'.",
                    ref.port.path,
                )
        item.port = ref
        item.target = target
    task = req.cell("task")
    if task:
        if task in world.model.tasks:
            item.task = task
        else:
            req.error(
                codes.TRG_TASK,
                f"Task '{task}' does not exist in the OS configuration of the project.",
            )
    if req.rejected or (kind in _PORT_BOUND and item.port is None):
        return None
    return item


# ---------------------------------------------------------------------- triggers: tasks


def _live_triggers(world: World, triggers: Sequence[Trigger]) -> list[Trigger]:
    """Triggers that no accepted row of this import deletes."""
    live: list[Trigger] = []
    for trigger in triggers:
        deleter = world.deleted_triggers.get(trigger.path)
        if deleter is None or deleter.root().rejected:
            live.append(trigger)
    return live


def _placement(
    world: World, req: Req, swc_path: str, runnable_name: str, named: str | None
) -> tuple[str | None, str | None] | None:
    """Task and predecessor event of a new trigger. None (with a Finding) when unclear."""
    runnable = world.model.find_runnable(swc_path, runnable_name)
    if runnable is None:
        return named, None  # new server runnable: unmapped unless the row names a task
    mapped: list[tuple[Trigger, TaskMapping]] = []
    for trigger in _live_triggers(world, world.model.triggers_of(runnable.path)):
        mapping = world.model.task_of(trigger.path)
        if mapping is not None:
            mapped.append((trigger, mapping))
    if named is not None:
        return named, _last_event([pair for pair in mapped if pair[1].task == named])
    tasks = sorted({mapping.task for _, mapping in mapped})
    if not tasks:
        return None, None
    if len(tasks) > 1:
        req.error(
            codes.TRG_TASK_AMBIGUOUS,
            f"Runnable {runnable_name} already runs in several tasks ({', '.join(tasks)}), so "
            f"the task of the new trigger is not clear. Name one in cell "
            f"'{header(_TRIGGERS, 'task')}'.",
            runnable.path,
        )
        return None
    return tasks[0], _last_event(mapped)


def _last_event(mapped: Sequence[tuple[Trigger, TaskMapping]]) -> str | None:
    if not mapped:
        return None
    ranked = max(
        enumerate(mapped),
        key=lambda entry: (
            entry[1][1].position if entry[1][1].position is not None else -1,
            entry[0],
        ),
    )
    return ranked[1][0].path


def _warn_other_task(world: World, item: _TriggerItem, trigger: Trigger) -> None:
    mapping = world.model.task_of(trigger.path)
    current = mapping.task if mapping is not None else None
    if item.task is not None and item.task != current:
        item.req.warn(
            codes.TRG_TASK_DIFFERS,
            f"The trigger already exists (event {trigger.name}), but it is "
            + (f"mapped to task {current}" if current else "not mapped to a task")
            + f", not to {item.task}. Nothing is changed; re-mapping an existing event is "
            "done in DaVinci Configurator.",
            trigger.path,
        )


# ---------------------------------------------------------------------- triggers: emitting


def _emit_trigger(
    world: World,
    req: Req,
    sheet: Sheet,
    component: Component,
    route: Route,
    runnable: str,
    kind: TriggerKind,
    port: PortRef | None,
    target: str | None,
    period_s: float | None,
    task: str | None,
    after: str | None,
) -> bool:
    port_name = port.port.name if port is not None else None
    op = AddTrigger(
        id=_ids.add_trigger(sheet, component.path, runnable, kind, port_name, target, period_s),
        **req.base(route),
        swc_path=component.path,
        runnable_name=runnable,
        kind=kind,
        port_name=port_name,
        target=target,
        period_s=period_s,
        task=task,
        position_after_event=after,
    )
    if not world.check_pending(req, op):
        return False
    is_model = route is Route.MODEL
    needs = (port.port.path,) if port is not None and not port.exists else ()
    req.emit(op, world.team_of(component) if is_model else None, needs if is_model else ())
    return True


def _emit_server_runnable(
    world: World,
    req: Req,
    sheet: Sheet,
    server: ServerPort | _TriggerItem,
    name: str,
    operation: str,
) -> bool:
    component = server.component
    port_name = server.name if isinstance(server, ServerPort) else (server.port_name or "")
    op = AddServerRunnable(
        id=_ids.add_server_runnable(sheet, component.path, name),
        **req.base(server.route),
        swc_path=component.path,
        name=name,
        symbol=name,
        port_name=port_name,
        operation=operation,
    )
    if not world.check_pending(req, op):
        return False
    is_model = server.route is Route.MODEL
    needs = (f"{component.path}/{port_name}",)
    req.emit(op, world.team_of(component) if is_model else None, needs if is_model else ())
    world.new_runnables[(component.path, name)] = req
    return True


def _new_runnable_problem(world: World, component: Component, name: str) -> tuple[str, str] | None:
    """Why ``name`` cannot be the name of a new server runnable of ``component``."""
    problems = world.names.check(name, NameKind.RUNNABLE)
    if problems:
        return problems[0][0], problems[0][1].rstrip(".")
    clash = scope_clash(world.behavior_scope(component.path), name)
    if clash is not None:
        code, other = clash
        return (
            code,
            f"SWC {component.name} already has a runnable or event named '{other}'",
        )
    if (component.path, name) in world.new_runnables:
        return (
            codes.NAME_DUPLICATE,
            f"This import already creates a server runnable '{name}' in SWC {component.name} "
            "for another operation (one new server runnable serves one operation)",
        )
    return None


def _server_trigger(world: World, item: _TriggerItem, replaced: dict[tuple[str, str], Req]) -> None:
    """An operation-invoked trigger row: assigns the runnable of one server operation."""
    req = item.req
    component = item.component
    port = item.port
    operation = item.target or ""
    if port is None:
        return
    label = f"Operation {operation} of server port {component.name}.{port.port.name}"
    if port.origin is PortOrigin.PENDING:
        req.error(
            codes.TRG_SERVER_TAKEN,
            f"{label}: the port is waiting in the pending queue, and that request already "
            "includes the runnable of every operation.",
            port.port.path,
        )
        return
    if port.exists:
        existing = world.server_triggers(component.path).get((port.port.path, operation), [])
        live = _live_triggers(world, existing)
        if live:
            runnable = world.model.runnables.get(live[0].runnable_path or "")
            current = runnable.name if runnable else last_of(live[0].runnable_path or "?")
            if current == item.runnable:
                req.already = True
                _warn_other_task(world, item, live[0])
            else:
                req.error(
                    codes.TRG_SERVER_TAKEN,
                    f"{label} is already served by runnable {current} (event {live[0].name}). "
                    "A server operation has exactly one runnable.",
                    live[0].path,
                )
            return
        req.depends.extend(world.deleted_triggers[t.path] for t in existing)
        if not world.has_runnable(req, component.path, item.runnable):
            req.error(
                codes.REF_UNKNOWN_RUNNABLE,
                f"SWC {component.name} has no runnable '{item.runnable}'. PortTool creates a "
                "runnable only for an operation of a server port that is new in the same "
                "import; create this runnable in DaVinci Developer.",
            )
            return
    elif world.model.find_runnable(component.path, item.runnable) is None:
        problem = _new_runnable_problem(world, component, item.runnable)
        if problem is not None:
            req.error(problem[0], f"Server runnable '{item.runnable}': {problem[1]}.")
            return
        if not _emit_server_runnable(world, req, _TRIGGERS, item, item.runnable, operation):
            return
    placement = _placement(world, req, component.path, item.runnable, item.task)
    if placement is None:
        return
    if _emit_trigger(
        world,
        req,
        _TRIGGERS,
        component,
        item.route,
        item.runnable,
        item.kind,
        port,
        operation,
        None,
        placement[0],
        placement[1],
    ):
        replaced[(port.port.path, operation)] = req


def _auto_runnables(
    world: World, servers: Sequence[ServerPort], claims: dict[ClaimKey, list[Req]]
) -> None:
    """Give every operation of every new server port exactly one runnable."""
    pattern = world.config.naming.server_runnable_pattern
    for server in servers:
        req = server.req
        component = server.component
        for operation in server.interface.operations:
            if req.rejected:
                break
            claimers = claims.get((component.path, server.name, operation.name), [])
            if claimers:
                accepted = [c.root() for c in claimers if not c.rejected and not c.root().rejected]
                if accepted:
                    req.depends.append(accepted[0])
                else:
                    req.error(
                        codes.REF_REJECTED_DEPENDENCY,
                        f"Operation {operation.name} of new server port {server.name} gets its "
                        f"runnable from {claimers[0].where}, but that request was rejected. A "
                        "server port is created only when every operation has a runnable.",
                    )
                continue
            name = world.fill_pattern(
                req,
                "server_runnable_pattern",
                pattern,
                port=server.name,
                operation=operation.name,
                swc=component.name,
            )
            if name is None:
                break
            problem = _new_runnable_problem(world, component, name)
            if problem is not None:
                req.error(
                    problem[0],
                    f"The runnable for operation {operation.name} of new server port "
                    f"{server.name} would be named '{name}' (pattern '{pattern}'). "
                    f"{problem[1]}. Add a row with trigger kind "
                    f"'{TriggerKind.OPERATION_INVOKED.value}' on the RunnableTriggers sheet "
                    "to choose the runnable name yourself.",
                )
                break
            if not _emit_server_runnable(world, req, Sheet.PORTS, server, name, operation.name):
                break
            port = world.new_ports.get((component.path, server.name))
            _emit_trigger(
                world,
                req,
                Sheet.PORTS,
                component,
                server.route,
                name,
                TriggerKind.OPERATION_INVOKED,
                port,
                operation.name,
                None,
                None,
                None,
            )


def _plain_trigger(world: World, item: _TriggerItem) -> None:
    """Every trigger row that is not an operation-invoked trigger."""
    req = item.req
    component = item.component
    if not world.has_runnable(req, component.path, item.runnable):
        req.error(
            codes.REF_UNKNOWN_RUNNABLE,
            f"SWC {component.name} has no runnable '{item.runnable}'. PortTool creates only "
            "the runnables of new server ports; create this runnable in DaVinci Developer.",
        )
        return
    runnable = world.model.find_runnable(component.path, item.runnable)
    port_path = item.port.port.path if item.port is not None else None
    if runnable is not None and (item.port is None or item.port.exists):
        for trigger in world.model.triggers_of(runnable.path):
            if (
                trigger.kind is not item.kind
                or (trigger.port_path or None) != port_path
                or (trigger.target or None) != item.target
            ):
                continue
            if item.period_s is not None and (
                trigger.period_s is None
                or abs(trigger.period_s - item.period_s) > _PERIOD_TOLERANCE_S
            ):
                continue
            deleter = world.deleted_triggers.get(trigger.path)
            if deleter is not None:
                for party, other in ((req, deleter), (deleter, req)):
                    party.error(
                        codes.CFL_ADD_DELETE,
                        f"Trigger {trigger.name} of runnable {item.runnable} is added in one "
                        f"row and deleted in another ({other.where}). Both rows are rejected.",
                        trigger.path,
                    )
                return
            req.already = True
            _warn_other_task(world, item, trigger)
            return
    placement = _placement(world, req, component.path, item.runnable, item.task)
    if placement is None:
        return
    _emit_trigger(
        world,
        req,
        _TRIGGERS,
        component,
        item.route,
        item.runnable,
        item.kind,
        item.port,
        item.target,
        item.period_s,
        placement[0],
        placement[1],
    )


def _check_deleted_server_triggers(world: World, replaced: dict[tuple[str, str], Req]) -> None:
    """A server operation must keep a runnable unless its port goes too."""
    for path, deleter in world.deleted_triggers.items():
        trigger = world.model.triggers[path]
        if (
            deleter.rejected
            or trigger.kind is not TriggerKind.OPERATION_INVOKED
            or not trigger.port_path
            or not trigger.target
        ):
            continue
        port_deleter = world.deleted_ports.get(trigger.port_path)
        if port_deleter is not None and not port_deleter.root().rejected:
            deleter.depends.append(port_deleter)
            continue
        replacement = replaced.get((trigger.port_path, trigger.target))
        if replacement is not None and not replacement.rejected:
            deleter.depends.append(replacement)
            continue
        others = world.server_triggers(trigger.swc_path).get(
            (trigger.port_path, trigger.target), []
        )
        if any(other.path != path for other in _live_triggers(world, others)):
            continue
        deleter.error(
            codes.TRG_SERVER_ORPHAN,
            f"Trigger {trigger.name} is the only one that runs operation {trigger.target} of "
            f"server port {last_of(trigger.port_path)}. Without it the operation has no "
            "runnable. Delete the port in the same import, or add a row that assigns "
            "another runnable.",
            path,
        )


def process_triggers(world: World, reqs: Sequence[Req], servers: Sequence[ServerPort]) -> None:
    """Validate the ADD rows of the RunnableTriggers sheet and create server runnables."""
    claims: dict[ClaimKey, list[Req]] = {}
    server_groups: dict[ClaimKey, list[_TriggerItem]] = {}
    plain_groups: dict[tuple[object, ...], list[_TriggerItem]] = {}
    for req in reqs:
        item = _parse_trigger(world, req, claims)
        if item is None:
            continue
        if item.kind is TriggerKind.OPERATION_INVOKED:
            key = (item.component.path, item.port_name or "", item.target or "")
            server_groups.setdefault(key, []).append(item)
        else:
            plain_groups.setdefault(
                (
                    item.component.path,
                    item.runnable,
                    item.kind,
                    item.port_name,
                    item.target,
                    item.period_s,
                ),
                [],
            ).append(item)

    replaced: dict[tuple[str, str], Req] = {}
    for (_, port_name, operation), members in server_groups.items():
        label = f"The runnable of operation {operation} of server port {port_name}"
        if settle([(m.req, (m.runnable, m.task)) for m in members], label) is not None:
            _server_trigger(world, members[0], replaced)
    _auto_runnables(world, servers, claims)
    for members in plain_groups.values():
        first = members[0]
        label = f"The trigger '{first.kind.value}' of runnable {first.runnable}"
        if settle([(m.req, m.task) for m in members], label) is not None:
            _plain_trigger(world, first)
    _check_deleted_server_triggers(world, replaced)


# ---------------------------------------------------------------------- access points


def _access_fit(kind: AccessKind, direction: PortDirection, interface: PortInterface) -> str:
    group = _ACCESS_CLASS[kind]
    data = interface.kind in SR_STYLE
    if group == "read":
        if not data or direction not in (PortDirection.R, PortDirection.PR):
            return "needs an R or PR port with a sender-receiver interface"
    elif group == "write":
        if not data or direction not in (PortDirection.P, PortDirection.PR):
            return "needs a P or PR port with a sender-receiver interface"
    elif group == "call":
        if interface.kind is not InterfaceKind.CLIENT_SERVER or direction is not PortDirection.R:
            return "needs an R port with a client-server interface (a client port)"
    elif interface.kind is not InterfaceKind.MODE_SWITCH:
        return "needs a port with a mode-switch interface"
    elif kind is AccessKind.MODE_SWITCH and direction is PortDirection.R:
        return "needs the P port of the mode manager"
    return ""


def _check_access_target(req: Req, item: _AccessItem, interface: PortInterface) -> None:
    owner = f"Interface {interface.name} of port {item.component.name}.{item.port.port.name}"
    if interface.kind is InterfaceKind.CLIENT_SERVER:
        if interface.operation(item.target) is None:
            req.error(
                codes.REF_UNKNOWN_TARGET,
                f"{owner} has no operation '{item.target}'.",
                interface.path,
            )
        return
    element = interface.element(item.target)
    if element is None:
        if interface.kind is InterfaceKind.MODE_SWITCH and not interface.elements:
            return  # the mode group name is not loaded: nothing to compare with
        req.error(
            codes.REF_UNKNOWN_TARGET,
            f"{owner} has no data element '{item.target}'.",
            interface.path,
        )
        return
    if interface.kind is InterfaceKind.MODE_SWITCH:
        return
    if item.kind is AccessKind.QUEUED_RECEIVE and not element.is_queued:
        req.error(
            codes.ACC_QUEUED,
            f"Access kind '{item.kind.value}' needs a queued data element, but {item.target} "
            f"of interface {interface.name} is not queued. Use an implicit or explicit read.",
        )
    elif element.is_queued and item.kind in (
        AccessKind.IMPLICIT_READ,
        AccessKind.IMPLICIT_WRITE,
        AccessKind.EXPLICIT_READ,
    ):
        req.error(
            codes.ACC_QUEUED,
            f"Data element {item.target} of interface {interface.name} is queued; access kind "
            f"'{item.kind.value}' is not possible for a queued element. Use "
            f"'{AccessKind.QUEUED_RECEIVE.value}' or '{AccessKind.EXPLICIT_SEND.value}'.",
        )


def _parse_access(world: World, req: Req) -> _AccessItem | None:
    if not require(req, _ACCESS, "swc", "runnable", "port", "target", "access_kind"):
        return None
    component = world.resolve_swc(req, req.cell("swc"))
    kind = parse_access_kind(req.cell("access_kind"))
    if kind is None or kind not in _ACCESS_CLASS:
        req.error(
            codes.ACC_KIND,
            f"Access kind '{req.cell('access_kind')}' is not supported in v1. Supported: "
            + ", ".join(k.value for k in _ACCESS_CLASS)
            + ".",
        )
        kind = None
    route = world.route_for(req, component) if component is not None else None
    if component is None or kind is None or route is None:
        return None
    ref = world.resolve_port(req, component, req.cell("port"))
    if not world.has_runnable(req, component.path, req.cell("runnable")):
        req.error(
            codes.REF_UNKNOWN_RUNNABLE,
            f"SWC {component.name} has no runnable '{req.cell('runnable')}'. PortTool creates "
            "only the runnables of new server ports; create this runnable in DaVinci Developer.",
        )
    if ref is None:
        return None
    item = _AccessItem(req, component, route, req.cell("runnable"), ref, req.cell("target"), kind)
    interface = world.port_interface(req, ref, component.name)
    if interface is not None:
        why = _access_fit(kind, ref.port.direction, interface)
        if why:
            req.error(
                codes.ACC_FIT,
                f"Access kind '{kind.value}' {why}; port {component.name}.{ref.port.name} is a "
                f"{ref.port.direction.value} port with the {interface.kind.value} interface "
                f"{interface.name}.",
                ref.port.path,
            )
        else:
            _check_access_target(req, item, interface)
    return None if req.rejected else item


def _add_access(world: World, item: _AccessItem) -> None:
    req = item.req
    component = item.component
    model = world.model
    runnable = model.find_runnable(component.path, item.runnable)
    if runnable is not None and item.port.exists:
        for access in model.access_points_of_runnable(runnable.path):
            if (
                access.port_path != item.port.port.path
                or access.target != item.target
                or _ACCESS_CLASS.get(access.kind) != _ACCESS_CLASS[item.kind]
            ):
                continue
            deleter = world.deleted_access.get(access.path)
            if access.kind is item.kind:
                if deleter is None:
                    req.already = True
                    return
                for party, other in ((req, deleter), (deleter, req)):
                    party.error(
                        codes.CFL_ADD_DELETE,
                        f"The {access.kind.value} of {item.port.port.name}.{item.target} by "
                        f"runnable {item.runnable} is added in one row and deleted in another "
                        f"({other.where}). Both rows are rejected.",
                        access.path,
                    )
                return
            if deleter is None or deleter.root().rejected:
                req.error(
                    codes.ACC_DUPLICATE,
                    f"Runnable {item.runnable} already has the access "
                    f"'{access.kind.value}' to {item.port.port.name}.{item.target}. It cannot "
                    f"also get '{item.kind.value}'; delete the existing access in the same "
                    "import to replace it.",
                    access.path,
                )
                return
            req.depends.append(deleter)
    op = AddAccessPoint(
        id=_ids.access_point(
            "add_access_point",
            component.path,
            item.runnable,
            item.port.port.name,
            item.target,
            item.kind,
        ),
        **req.base(item.route),
        swc_path=component.path,
        runnable_name=item.runnable,
        port_name=item.port.port.name,
        target=item.target,
        kind=item.kind,
    )
    if not world.check_pending(req, op):
        return
    is_model = item.route is Route.MODEL
    needs = (item.port.port.path,) if not item.port.exists else ()
    req.emit(op, world.team_of(component) if is_model else None, needs if is_model else ())


def process_access(world: World, reqs: Sequence[Req]) -> None:
    """Validate the ADD rows of the RunnableAccess sheet."""
    groups: dict[tuple[str, str, str, str, str], list[_AccessItem]] = {}
    for req in reqs:
        item = _parse_access(world, req)
        if item is not None:
            key = (
                item.component.path,
                item.runnable,
                item.port.port.name,
                item.target,
                _ACCESS_CLASS[item.kind],
            )
            groups.setdefault(key, []).append(item)
    for members in groups.values():
        first = members[0]
        label = f"The access of runnable {first.runnable} to {first.port.port.name}.{first.target}"
        if settle([(member.req, member.kind) for member in members], label) is not None:
            _add_access(world, first)
