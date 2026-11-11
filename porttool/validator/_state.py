"""Mutable per-request state used while one ``validate()`` call runs. Internal."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypedDict

from porttool.core.operations import AnyOperation, Route
from porttool.core.results import Finding, RawRequestRow, RequestStatus, RowRef, Severity
from porttool.core.sheets import Action, Sheet
from porttool.validator import codes


class OperationBaseFields(TypedDict):
    """The ``OperationBase`` fields every operation of one request shares."""

    requester: str
    comment: str
    route: Route
    source_file: str | None
    source_sheet: str | None
    source_row: int | None


def short_file(path: str) -> str:
    """Last component of a workbook path (the path may use ``/`` or ``\\``)."""
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def describe_row(row: RowRef) -> str:
    return f"row {row.row} of sheet {row.sheet} in {short_file(row.file)}"


@dataclass(slots=True, eq=False)
class Emitted:
    """One operation of a request, with what the pending queue needs to know about it."""

    op: AnyOperation
    owner_team: str | None = None
    depends_on: tuple[str, ...] = ()


@dataclass(slots=True, eq=False)
class Req:
    """One request row and everything the validator decided about it."""

    index: int
    raw: RawRequestRow
    request_id: str
    sheet: Sheet | None = None
    action: Action | None = None
    row_id: str | None = None
    findings: list[Finding] = field(default_factory=list)
    rejected: bool = False
    already: bool = False
    emitted: list[Emitted] = field(default_factory=list)
    #: Status and operation ids are taken from this request (merged duplicate, interface row).
    leader: Req | None = None
    #: Requests that must not be rejected for this one to stand.
    depends: list[Req] = field(default_factory=list)

    # ---- cells

    def cell(self, key: str) -> str:
        return (self.raw.values.get(key) or "").strip()

    # ---- findings

    def _add(self, code: str, severity: Severity, message: str, object_path: str | None) -> None:
        self.findings.append(
            Finding(
                code=code,
                severity=severity,
                message=message,
                object_path=object_path,
                row=self.raw.row,
            )
        )

    def error(self, code: str, message: str, object_path: str | None = None) -> None:
        """Add an ERROR finding and reject the request."""
        self._add(code, Severity.ERROR, message, object_path)
        self.rejected = True

    def warn(self, code: str, message: str, object_path: str | None = None) -> None:
        self._add(code, Severity.WARNING, message, object_path)

    def info(self, code: str, message: str, object_path: str | None = None) -> None:
        self._add(code, Severity.INFO, message, object_path)

    def problems(self, found: list[tuple[str, str]], object_path: str | None = None) -> bool:
        """Add ``(code, message)`` pairs as errors. True when there was at least one."""
        for code, message in found:
            self.error(code, message, object_path)
        return bool(found)

    # ---- relations

    @property
    def where(self) -> str:
        return describe_row(self.raw.row)

    def root(self) -> Req:
        request = self
        while request.leader is not None:
            request = request.leader
        return request

    def follow(self, leader: Req) -> None:
        """Take status and operations from ``leader`` (never from a follower of it)."""
        self.leader = leader.root()

    def merge_into(self, leader: Req) -> None:
        """Mark this request as a repetition of ``leader`` and say so on both rows."""
        self.follow(leader)
        self.info(
            codes.CFL_MERGED,
            f"This request repeats {leader.where}; both rows are handled as one request.",
        )
        leader.info(
            codes.CFL_MERGED,
            f"The same request is also in {self.where}; both rows are handled as one request.",
        )

    def base(self, route: Route = Route.APPLY) -> OperationBaseFields:
        row = self.raw.row
        return {
            "requester": self.raw.requester,
            "comment": self.raw.comment,
            "route": route,
            "source_file": row.file,
            "source_sheet": row.sheet,
            "source_row": row.row,
        }

    def emit(
        self, op: AnyOperation, owner_team: str | None = None, depends_on: tuple[str, ...] = ()
    ) -> None:
        self.emitted.append(Emitted(op=op, owner_team=owner_team, depends_on=depends_on))

    # ---- outcome

    def status(self) -> RequestStatus:
        root = self.root()
        if self.rejected or root.rejected:
            return RequestStatus.REJECTED
        if root.already:
            return RequestStatus.ALREADY_APPLIED
        routes = {item.op.route for item in root.emitted}
        if Route.HOLD in routes:
            return RequestStatus.ON_HOLD
        if Route.MODEL in routes:
            return RequestStatus.WAITING_FOR_MODEL_OWNER
        return RequestStatus.ACCEPTED

    @property
    def decided(self) -> bool:
        root = self.root()
        return self.rejected or root.rejected or root.already or bool(root.emitted)
