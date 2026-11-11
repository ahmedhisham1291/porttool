"""The validator: request rows in, change set, results and pending queue out.

``validate`` is a pure function. It reads the immutable model, never touches a file or a
tool, takes the time from ``now_utc`` and reports every problem of a request as a
``Finding``. The phases run in dependency order, so a row may refer to an object that
another row of the same import creates, whatever the order of the rows:

1. classify rows (sheet, action, RowID),
2. review the pending queue,
3. resolve MODIFY and DELETE rows,
4. data types, 5. interfaces, 6. ports, 7. triggers and server runnables,
8. access points, 9. held connectors and new connectors,
10. reject requests that need a rejected request, 11. assemble the outcome.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from porttool.core.config import ProjectConfig
from porttool.core.errors import PortToolError
from porttool.core.interfaces import ValidationOutcome
from porttool.core.models import Model
from porttool.core.operations import APPLY_ORDER, AnyOperation, ChangeSet, Route
from porttool.core.pending import PendingEntry, PendingQueue, PendingState
from porttool.core.results import RawRequestRow, RequestResult, RequestStatus, Severity
from porttool.core.sheets import REQUEST_SHEETS, Action, Sheet
from porttool.validator import codes
from porttool.validator._behavior import process_access, process_triggers
from porttool.validator._changes import process_changes
from porttool.validator._connectors import Wiring, process_connectors, release_held
from porttool.validator._datatypes import process_data_types
from porttool.validator._interfaces import process_interfaces
from porttool.validator._pending import PendingReview, review_pending
from porttool.validator._ports import process_ports
from porttool.validator._state import Req
from porttool.validator._world import World

#: MODIFY and DELETE requests that have an operation in the v1 contract.
_SUPPORTED_CHANGES: frozenset[tuple[Sheet, Action]] = frozenset(
    {
        (Sheet.PORTS, Action.MODIFY),
        (Sheet.PORTS, Action.DELETE),
        (Sheet.CONNECTIONS, Action.DELETE),
        (Sheet.RUNNABLE_ACCESS, Action.DELETE),
        (Sheet.RUNNABLE_TRIGGERS, Action.DELETE),
    }
)

_APPLY_POSITION = {kind: position for position, kind in enumerate(APPLY_ORDER)}
_LIVE = (
    RequestStatus.ACCEPTED,
    RequestStatus.WAITING_FOR_MODEL_OWNER,
    RequestStatus.ON_HOLD,
)


def request_id_of(row: RawRequestRow) -> str:
    """Id of the request of one workbook row: ``<workbook>#<sheet>#<row number>``."""
    return f"{row.row.file}#{row.row.sheet}#{row.row.row}"


def _classify(index: int, row: RawRequestRow, taken: set[str]) -> Req:
    """Create the request state of one row and apply the rules that need no model."""
    request_id = request_id_of(row)
    serial = 1
    while request_id in taken:  # two rows with the same position: keep the ids unique
        serial += 1
        request_id = f"{request_id_of(row)}~{serial}"
    taken.add(request_id)
    req = Req(index=index, raw=row, request_id=request_id)
    try:
        sheet = Sheet(row.sheet.strip())
    except ValueError:
        sheet = None
    if sheet is None or sheet not in REQUEST_SHEETS:
        req.error(
            codes.ROW_UNKNOWN_SHEET,
            f"Sheet '{row.sheet}' takes no requests. Requests are read from the sheets "
            f"{', '.join(s.value for s in REQUEST_SHEETS)}.",
        )
        return req
    req.sheet = sheet
    try:
        req.action = Action(row.action.strip().upper())
    except ValueError:
        req.error(
            codes.ROW_UNKNOWN_ACTION,
            f"Action '{row.action}' is not known. Use ADD, MODIFY or DELETE."
            if row.action.strip()
            else "The Action cell is empty. Use ADD, MODIFY or DELETE.",
        )
        return req
    req.row_id = (row.row_id or "").strip() or None
    if req.action is Action.ADD:
        if req.row_id is not None:
            req.error(
                codes.ROW_ADD_WITH_ROWID,
                "Action ADD is set on a row that describes an existing object (it has a "
                "RowID). Use MODIFY or DELETE for an existing object, or enter the new object "
                "in an empty row.",
            )
        return req
    if req.row_id is None:
        req.error(
            codes.ROW_CHANGE_WITHOUT_ROWID,
            f"Action {req.action.value} is set on a new row (no RowID). Only rows exported "
            "from the repo can be changed or deleted; use ADD for a new object.",
        )
    if (sheet, req.action) not in _SUPPORTED_CHANGES:
        req.error(
            codes.ROW_UNSUPPORTED,
            f"{req.action.value} is not supported on sheet {sheet.value} in v1. Supported: "
            "MODIFY and DELETE of ports, DELETE of connections, runnable access and "
            "runnable triggers. Use DaVinci Developer for this change.",
        )
    return req


def _cascade(reqs: Sequence[Req]) -> None:
    """Reject every request that needs a rejected request, until nothing changes."""
    changed = True
    while changed:
        changed = False
        for req in reqs:
            if req.rejected or req.leader is not None:
                continue
            for needed in req.depends:
                if needed is req or not (needed.rejected or needed.root().rejected):
                    continue
                req.error(
                    codes.REF_REJECTED_DEPENDENCY,
                    f"This request needs the request in {needed.where}, which was rejected.",
                )
                changed = True
                break


def _unique_operations(world: World, reqs: Sequence[Req]) -> list[AnyOperation]:
    """Operations of all standing requests, released connectors first, ids made unique."""
    operations: list[AnyOperation] = list(world.released.values())
    taken = {op.id for op in operations}
    for req in reqs:
        if req.leader is not None or req.status() not in _LIVE:
            continue
        for emitted in req.emitted:
            op = emitted.op
            if world.released.get(op.id) is op:
                continue  # a row that repeats a released connector shares its operation
            if op.id in taken:
                serial = 2
                while f"{op.id}~{serial}" in taken:
                    serial += 1
                op = op.model_copy(update={"id": f"{op.id}~{serial}"})
                emitted.op = op
            taken.add(op.id)
            operations.append(op)
    operations.sort(key=lambda op: _APPLY_POSITION[op.op])
    return operations


def _results(reqs: Sequence[Req]) -> tuple[RequestResult, ...]:
    results: list[RequestResult] = []
    for req in reqs:
        if not req.decided:  # pragma: no cover - a rule module forgot to decide a row
            raise PortToolError(
                f"The validator did not decide the request in {req.where}.",
                detail=req.request_id,
            )
        root = req.root()
        status = req.status()
        findings = list(req.findings)
        if root is not req and root.rejected and not req.rejected:
            findings.extend(
                replace(finding, row=req.raw.row)
                for finding in root.findings
                if finding.severity is Severity.ERROR
            )
        results.append(
            RequestResult(
                request_id=req.request_id,
                status=status,
                row=req.raw.row,
                findings=tuple(findings),
                operation_ids=tuple(e.op.id for e in root.emitted) if status in _LIVE else (),
            )
        )
    return tuple(results)


def _pending_queue(
    world: World, reqs: Sequence[Req], review: PendingReview, old: PendingQueue
) -> PendingQueue:
    """Entries that stay, then one new entry per waiting or held operation of this import."""
    entries = review.kept()
    queued = {entry.operation.id for entry in entries}
    for req in reqs:
        if req.leader is not None or req.status() not in _LIVE:
            continue
        for emitted in req.emitted:
            op = emitted.op
            if op.route is Route.APPLY or op.id in queued:
                continue
            queued.add(op.id)
            entries.append(
                PendingEntry(
                    operation=op,
                    state=(
                        PendingState.ON_HOLD
                        if op.route is Route.HOLD
                        else PendingState.WAITING_FOR_MODEL_OWNER
                    ),
                    created_utc=world.now_utc,
                    owner_team=emitted.owner_team,
                    depends_on=emitted.depends_on,
                    note=f"Requested in {req.where}.",
                )
            )
    return PendingQueue(format_version=old.format_version, entries=tuple(entries))


def validate(
    model: Model,
    config: ProjectConfig,
    rows: Sequence[RawRequestRow],
    pending: PendingQueue,
    now_utc: str,
) -> ValidationOutcome:
    """Validate ``rows`` against ``model``. See the module docstring for the phases."""
    world = World(model, config, now_utc)
    for code, message in world.names.config_problems:
        world.add_finding(code, message)
    taken: set[str] = set()
    reqs = [_classify(index, row, taken) for index, row in enumerate(rows)]

    review = review_pending(world, pending)
    changes = process_changes(world, reqs)

    adds: dict[Sheet, list[Req]] = {sheet: [] for sheet in REQUEST_SHEETS}
    for req in reqs:
        if not req.rejected and req.sheet is not None and req.action is Action.ADD:
            adds[req.sheet].append(req)
    process_data_types(world, adds[Sheet.DATA_TYPES])
    process_interfaces(world, adds[Sheet.INTERFACES], adds[Sheet.INTERFACE_ELEMENTS])
    servers = process_ports(world, adds[Sheet.PORTS], changes)
    process_triggers(world, adds[Sheet.RUNNABLE_TRIGGERS], servers)
    process_access(world, adds[Sheet.RUNNABLE_ACCESS])
    wiring = Wiring(world)
    release_held(world, wiring, review.held)
    process_connectors(world, wiring, adds[Sheet.CONNECTIONS])

    _cascade(reqs)
    operations = _unique_operations(world, reqs)
    return ValidationOutcome(
        change_set=ChangeSet(
            baseline_commit=model.baseline_commit,
            model_hash=model.model_hash,
            created_utc=now_utc,
            operations=tuple(operations),
        ),
        results=_results(reqs),
        pending=_pending_queue(world, reqs, review, pending),
        findings=tuple(world.findings),
    )


class ChangeValidator:
    """The ``porttool.core.interfaces.Validator`` of PortTool. Stateless and thread-safe."""

    def validate(
        self,
        model: Model,
        config: ProjectConfig,
        rows: Sequence[RawRequestRow],
        pending: PendingQueue,
        now_utc: str,
    ) -> ValidationOutcome:
        return validate(model, config, rows, pending, now_utc)
