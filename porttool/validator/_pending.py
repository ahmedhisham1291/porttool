"""Review of the pending queue at the start of a run. Internal.

Every entry is looked at before any new row is judged: entries the repo now fulfils are
closed, mismatches and old entries are reported, and ports that are still awaited from a
model owner are made known so that connectors to them can be held. No entry is dropped
without a Finding. Held connectors are released by ``_connectors.release_held``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from porttool.core.models import Port, TriggerKind
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
    Route,
)
from porttool.core.pending import PendingEntry, PendingQueue
from porttool.validator import codes
from porttool.validator._ports import same_value
from porttool.validator._world import PortOrigin, PortRef, World, last_of

_PERIOD_TOLERANCE_S = 1e-9


@dataclass(slots=True)
class QueueItem:
    entry: PendingEntry
    keep: bool = True


@dataclass(slots=True)
class PendingReview:
    items: list[QueueItem]
    #: Held connectors that are neither fulfilled nor orphaned: candidates for release.
    held: list[QueueItem]

    def kept(self) -> list[PendingEntry]:
        return [item.entry for item in self.items if item.keep]


def describe_operation(op: AnyOperation) -> str:
    """Short text that tells a user which request an operation is."""
    if isinstance(op, AddPort):
        return (
            f"new port {last_of(op.swc_path)}.{op.name} ({op.direction.value}, "
            f"{last_of(op.interface_path)})"
        )
    if isinstance(op, ModifyPort):
        return f"change of port {last_of(op.swc_path)}.{op.name}"
    if isinstance(op, DeletePort):
        return f"deletion of port {last_of(op.swc_path)}.{op.name}"
    if isinstance(op, AddServerRunnable):
        return (
            f"new server runnable {op.name} of {last_of(op.swc_path)} for "
            f"{op.port_name}.{op.operation}"
        )
    if isinstance(op, AddTrigger):
        return (
            f"new trigger '{op.kind.value}' of runnable {last_of(op.swc_path)}.{op.runnable_name}"
        )
    if isinstance(op, AddAccessPoint | DeleteAccessPoint):
        verb = "new" if isinstance(op, AddAccessPoint) else "deletion of"
        return (
            f"{verb} access '{op.kind.value}' of runnable {last_of(op.swc_path)}."
            f"{op.runnable_name} to {op.port_name}.{op.target}"
        )
    if isinstance(op, DeleteTrigger):
        return f"deletion of trigger {op.name} of {last_of(op.swc_path)}"
    if isinstance(op, AddConnector):
        return (
            f"connector {op.provider_prototype}.{op.provider_port} -> "
            f"{op.requester_prototype}.{op.requester_port} in {last_of(op.composition_path)}"
        )
    if isinstance(op, DeleteConnector):
        return f"deletion of connector {op.name} in {last_of(op.composition_path)}"
    return f"new {'data type' if isinstance(op, AddDataType) else 'interface'} {op.name}"


def _origin(entry: PendingEntry) -> str:
    who = entry.operation.requester or "unknown requester"
    return f"requested {entry.created_utc or 'at an unknown time'} by {who}"


def _parse_utc(text: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _flag_age(world: World, entry: PendingEntry, now: datetime | None) -> None:
    if now is None:
        return
    limit = world.config.pending_max_age_days
    created = _parse_utc(entry.created_utc)
    what = describe_operation(entry.operation)
    if created is None:
        world.add_finding(
            codes.PEND_STALE,
            f"Pending request ({what}) has the unreadable creation time "
            f"'{entry.created_utc}', so its age cannot be checked. Review it.",
        )
        return
    age_days = (now - created).total_seconds() / 86400.0
    if age_days > limit:
        team = f" Owner team: {entry.owner_team}." if entry.owner_team else ""
        world.add_finding(
            codes.PEND_STALE,
            f"Pending request ({what}, {_origin(entry)}) has been {entry.state.value} for "
            f"{int(age_days)} days; the limit is {limit} days.{team} Follow it up or remove it.",
        )


def _trigger_exists(world: World, op: AddTrigger) -> bool:
    model = world.model
    port = model.find_port(op.swc_path, op.port_name) if op.port_name else None
    if op.kind is TriggerKind.OPERATION_INVOKED:
        return bool(
            port is not None
            and op.target
            and world.server_triggers(op.swc_path).get((port.path, op.target))
        )
    runnable = model.find_runnable(op.swc_path, op.runnable_name)
    if runnable is None or (op.port_name and port is None):
        return False
    for trigger in model.triggers_of(runnable.path):
        if (
            trigger.kind is op.kind
            and (trigger.port_path or None) == (port.path if port is not None else None)
            and (trigger.target or None) == op.target
            and (
                op.period_s is None
                or (
                    trigger.period_s is not None
                    and abs(trigger.period_s - op.period_s) <= _PERIOD_TOLERANCE_S
                )
            )
        ):
            return True
    return False


def _access_exists(world: World, op: AddAccessPoint | DeleteAccessPoint) -> bool:
    model = world.model
    runnable = model.find_runnable(op.swc_path, op.runnable_name)
    port = model.find_port(op.swc_path, op.port_name)
    if runnable is None or port is None:
        return False
    return any(
        access.port_path == port.path and access.target == op.target and access.kind is op.kind
        for access in model.access_points_of_runnable(runnable.path)
    )


def _port_modified(port: Port, op: ModifyPort) -> bool:
    if op.interface_path is not None and port.interface_path != op.interface_path:
        return False
    current = {spec.target: spec.init_value for spec in port.com_specs}
    return all(same_value(spec.value, current.get(spec.element)) for spec in op.init_values)


def _review_entry(world: World, item: QueueItem, review: PendingReview) -> None:
    """Decide one entry: close it, report it, or keep it waiting."""
    model = world.model
    entry = item.entry
    op = entry.operation
    what = describe_operation(op)

    def close(detail: str = "") -> None:
        item.keep = False
        world.add_finding(
            codes.PEND_FULFILLED,
            f"Pending request ({what}, {_origin(entry)}) is fulfilled by the repo and was "
            f"closed.{' ' + detail if detail else ''}",
        )

    def orphan(missing: str) -> None:
        world.add_finding(
            codes.PEND_ORPHAN,
            f"Pending request ({what}, {_origin(entry)}) cannot be checked: {missing} no "
            "longer exists in the repo. The entry is kept; remove it if it is obsolete.",
        )

    if isinstance(op, AddConnector):
        review.held.append(item)
        return
    if isinstance(op, DeleteConnector):
        if f"{op.composition_path}/{op.name}" not in model.connectors:
            close()
        return
    if isinstance(op, AddDataType | AddInterface):
        path = f"{op.package_path}/{op.name}"
        if path in model.data_types or path in model.interfaces:
            close()
        return
    if op.swc_path not in model.components:
        orphan(f"SWC {op.swc_path}")
        return
    if isinstance(op, AddPort):
        port = model.find_port(op.swc_path, op.name)
        if port is None:
            world.pending_ports[(op.swc_path, op.name)] = PortRef(
                port=Port(
                    path=f"{op.swc_path}/{op.name}",
                    name=op.name,
                    swc_path=op.swc_path,
                    direction=op.direction,
                    interface_path=op.interface_path,
                ),
                origin=PortOrigin.PENDING,
                route=Route.MODEL,
            )
        elif port.direction is op.direction and port.interface_path == op.interface_path:
            close()
        else:
            world.add_finding(
                codes.PEND_MISMATCH,
                f"Pending request ({what}, {_origin(entry)}): the port now exists, but as "
                f"{port.direction.value} port with interface "
                f"{port.interface_path or '(none)'} instead of {op.direction.value} with "
                f"{op.interface_path}. The entry is kept; clarify with the model owner"
                f"{' (' + entry.owner_team + ')' if entry.owner_team else ''}.",
                port.path,
            )
    elif isinstance(op, ModifyPort):
        port = model.find_port(op.swc_path, op.name)
        if port is None:
            orphan(f"port {last_of(op.swc_path)}.{op.name}")
        elif _port_modified(port, op):
            close()
    elif isinstance(op, DeletePort):
        if model.find_port(op.swc_path, op.name) is None:
            close()
    elif isinstance(op, AddServerRunnable):
        port = model.find_port(op.swc_path, op.port_name)
        served = (
            world.server_triggers(op.swc_path).get((port.path, op.operation), [])
            if port is not None
            else []
        )
        if served:
            runnable = model.runnables.get(served[0].runnable_path or "")
            actual = runnable.name if runnable is not None else "?"
            close("" if actual == op.name else f"The model owner named the runnable {actual}.")
    elif isinstance(op, AddTrigger):
        if _trigger_exists(world, op):
            close()
    elif isinstance(op, AddAccessPoint):
        if _access_exists(world, op):
            close()
    elif isinstance(op, DeleteAccessPoint):
        if not _access_exists(world, op):
            close()
    elif isinstance(op, DeleteTrigger) and not any(
        trigger.name == op.name for trigger in model.triggers_in(op.swc_path)
    ):
        close()


def review_pending(world: World, queue: PendingQueue) -> PendingReview:
    """Look at every entry once. Fills ``world.pending_ports`` and ``world.pending_by_id``."""
    now = _parse_utc(world.now_utc)
    review = PendingReview(items=[QueueItem(entry) for entry in queue.entries], held=[])
    for item in review.items:
        _flag_age(world, item.entry, now)
        _review_entry(world, item, review)
        if item.keep:
            world.pending_by_id.setdefault(item.entry.operation.id, item.entry)
    return review
