"""Verification after the write: does the reloaded model hold what was requested?"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Mapping, Sequence

from porttool.core.models import ConnectorEnd, Model, Trigger
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
from porttool.core.results import Finding, Severity
from porttool.pipeline import codes
from porttool.pipeline.describe import operation_text

#: Model collections checked for lost objects.
_COLLECTIONS = (
    "components",
    "ports",
    "interfaces",
    "runnables",
    "access_points",
    "triggers",
    "prototypes",
    "connectors",
    "data_types",
    "base_types",
    "compu_methods",
    "constraints",
    "units",
    "mapping_sets",
    "constants",
    "task_mappings",
)
_MAX_LISTED = 20


def find_trigger(model: Model, op: AddTrigger, exclude: Collection[str] = ()) -> Trigger | None:
    """The trigger ``op`` asked for, ignoring trigger paths in ``exclude``."""
    runnable = model.find_runnable(op.swc_path, op.runnable_name)
    if runnable is None:
        return None
    port = model.find_port(op.swc_path, op.port_name) if op.port_name else None
    for trigger in model.triggers_of(runnable.path):
        if trigger.path in exclude or trigger.kind is not op.kind:
            continue
        if op.port_name and (port is None or trigger.port_path != port.path):
            continue
        if op.target is not None and trigger.target != op.target:
            continue
        if op.period_s is not None and (
            trigger.period_s is None or abs(trigger.period_s - op.period_s) > 1e-9
        ):
            continue
        return trigger
    return None


def _access_path(model: Model, op: AddAccessPoint | DeleteAccessPoint) -> str | None:
    runnable = model.find_runnable(op.swc_path, op.runnable_name)
    port = model.find_port(op.swc_path, op.port_name)
    if runnable is None or port is None:
        return None
    for access in model.access_points_of_runnable(runnable.path):
        if access.port_path == port.path and access.target == op.target and access.kind is op.kind:
            return access.path
    return None


def _connector_problem(model: Model, op: AddConnector) -> tuple[str, str] | None:
    connector = model.connectors.get(f"{op.composition_path}/{op.name}")
    if connector is None:
        return codes.VER_MISSING, "the connector does not exist"
    for side, prototype_name, port_name, end in (
        ("provider", op.provider_prototype, op.provider_port, connector.provider),
        ("requester", op.requester_prototype, op.requester_port, connector.requester),
    ):
        if not prototype_name:
            continue  # the composition's own port: not checked in detail
        prototype = model.find_prototype(op.composition_path, prototype_name)
        port = (
            model.find_port(prototype.type_path, port_name)
            if prototype is not None and prototype.type_path is not None
            else None
        )
        if prototype is None or port is None:
            return codes.VER_MISMATCH, f"the {side} port {prototype_name}.{port_name} is missing"
        if end != ConnectorEnd(prototype_path=prototype.path, port_path=port.path):
            return codes.VER_MISMATCH, f"the {side} end is not {prototype_name}.{port_name}"
    return None


def _problem(old: Model, new: Model, op: AnyOperation) -> tuple[str, str] | None:
    """(code, what is wrong) for one applied operation, or None when it is in the model."""
    if isinstance(op, AddDataType):
        data_type = new.data_types.get(f"{op.package_path}/{op.name}")
        if data_type is None:
            return codes.VER_MISSING, "the data type does not exist"
        if data_type.level is not op.level:
            return codes.VER_MISMATCH, f"the level is {data_type.level.value}"
    elif isinstance(op, AddInterface):
        interface = new.interfaces.get(f"{op.package_path}/{op.name}")
        if interface is None:
            return codes.VER_MISSING, "the interface does not exist"
        if interface.kind is not op.kind:
            return codes.VER_MISMATCH, f"the kind is {interface.kind.value}"
        have = {element.name for element in interface.elements}
        have |= {operation.name for operation in interface.operations}
        wanted = [e.name for e in op.elements] + [o.name for o in op.operations]
        missing = [name for name in wanted if name not in have]
        if missing:
            return codes.VER_MISMATCH, f"missing elements or operations: {', '.join(missing)}"
    elif isinstance(op, AddPort | ModifyPort):
        port = new.find_port(op.swc_path, op.name)
        if port is None:
            return codes.VER_MISSING, "the port does not exist"
        if isinstance(op, AddPort) and port.direction is not op.direction:
            return codes.VER_MISMATCH, f"the direction is {port.direction.value}"
        if op.interface_path is not None and port.interface_path != op.interface_path:
            return codes.VER_MISMATCH, f"the interface is {port.interface_path}"
    elif isinstance(op, AddServerRunnable):
        if new.find_runnable(op.swc_path, op.name) is None:
            return codes.VER_MISSING, "the runnable does not exist"
    elif isinstance(op, AddAccessPoint):
        if _access_path(new, op) is None:
            return codes.VER_MISSING, "the access point does not exist"
    elif isinstance(op, AddTrigger):
        if find_trigger(new, op) is None:
            return codes.VER_MISSING, "the trigger does not exist"
    elif isinstance(op, AddConnector):
        return _connector_problem(new, op)
    elif isinstance(op, DeletePort):
        if new.find_port(op.swc_path, op.name) is not None:
            return codes.VER_STILL_THERE, "the port still exists"
    elif isinstance(op, DeleteConnector):
        if f"{op.composition_path}/{op.name}" in new.connectors:
            return codes.VER_STILL_THERE, "the connector still exists"
    elif isinstance(op, DeleteAccessPoint):
        if _access_path(new, op) is not None:
            return codes.VER_STILL_THERE, "the access point still exists"
    elif isinstance(op, DeleteTrigger):
        if any(t.name == op.name for t in new.triggers_in(op.swc_path)):
            return codes.VER_STILL_THERE, "the trigger still exists"
    return None


def _expected_deletions(old: Model, operations: Sequence[AnyOperation]) -> set[str]:
    gone: set[str] = set()
    for op in operations:
        if isinstance(op, DeletePort):
            gone.add(f"{op.swc_path}/{op.name}")
        elif isinstance(op, DeleteConnector):
            gone.add(f"{op.composition_path}/{op.name}")
        elif isinstance(op, DeleteTrigger):
            gone.update(t.path for t in old.triggers_in(op.swc_path) if t.name == op.name)
        elif isinstance(op, DeleteAccessPoint):
            path = _access_path(old, op)
            if path is not None:
                gone.add(path)
    return gone


def verify_model(old: Model, new: Model, operations: Sequence[AnyOperation]) -> tuple[Finding, ...]:
    """Compare the model before the apply with the reloaded one. Pure.

    * every applied operation's object exists as requested (deleted ones are gone);
    * no object that existed before is gone, except what a delete request names;
    * the reload reports no error that the old model did not have.
    """
    findings: list[Finding] = []
    for op in operations:
        problem = _problem(old, new, op)
        if problem is not None:
            code, what = problem
            findings.append(
                Finding(
                    code=code,
                    severity=Severity.ERROR,
                    message=f"After the reload, {operation_text(op)} is not as requested: {what}.",
                    object_path=op.id,
                )
            )
    allowed = _expected_deletions(old, operations)
    lost: list[str] = []
    for name in _COLLECTIONS:
        before: Mapping[str, object] = getattr(old, name, {})
        after: Mapping[str, object] = getattr(new, name, {})
        lost.extend(sorted(path for path in before if path not in after and path not in allowed))
    if lost:
        shown = ", ".join(lost[:_MAX_LISTED])
        more = f" and {len(lost) - _MAX_LISTED} more" if len(lost) > _MAX_LISTED else ""
        findings.append(
            Finding(
                code=codes.VER_LOST,
                severity=Severity.ERROR,
                message=f"{len(lost)} object(s) that existed before the apply are gone: "
                f"{shown}{more}.",
            )
        )

    def keys(model: Model) -> Counter[tuple[str, str | None, str | None]]:
        return Counter(
            (f.code, f.object_path, f.file) for f in model.findings if f.severity is Severity.ERROR
        )

    new_errors = keys(new) - keys(old)
    if new_errors:
        texts = [
            f"{f.code}: {f.message}"
            for f in new.findings
            if f.severity is Severity.ERROR and (f.code, f.object_path, f.file) in new_errors
        ]
        findings.append(
            Finding(
                code=codes.VER_NEW_ERRORS,
                severity=Severity.ERROR,
                message=f"The reloaded workspace has {sum(new_errors.values())} new error(s): "
                + " | ".join(texts[:5]),
            )
        )
    return tuple(findings)
