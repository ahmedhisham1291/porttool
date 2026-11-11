"""Findings and results. CONTRACT FILE.

A ``Finding`` is how PortTool reports an expected problem to the user.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class RowRef:
    """Where a request came from: workbook file, sheet name, 1-based row number."""

    file: str
    sheet: str
    row: int


@dataclass(frozen=True, slots=True)
class Finding:
    """One problem or note. ``code`` is stable (e.g. ``VAL-NAME-001``), ``message`` is for users."""

    code: str
    severity: Severity
    message: str
    object_path: str | None = None
    file: str | None = None
    row: RowRef | None = None


class RequestStatus(StrEnum):
    ACCEPTED = "accepted"  # dry run: will be applied
    APPLIED = "applied"
    ALREADY_APPLIED = "already applied"
    REJECTED = "rejected"
    WAITING_FOR_MODEL_OWNER = "waiting for model owner"
    ON_HOLD = "on hold"


@dataclass(frozen=True, slots=True)
class RawRequestRow:
    """One edited workbook row, as text. Produced by the workbook package, judged by the validator.

    ``values`` maps the column keys of ``porttool.core.sheets`` to stripped cell text.
    ``action`` is upper-cased cell text (expected: ADD, MODIFY, DELETE).
    """

    sheet: str
    row: RowRef
    row_id: str | None
    action: str
    requester: str
    comment: str
    values: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class RequestResult:
    request_id: str
    status: RequestStatus
    row: RowRef | None = None
    findings: tuple[Finding, ...] = ()
    operation_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class JobResult:
    ok: bool
    findings: tuple[Finding, ...] = ()
    elapsed_s: float = 0.0
    data: object | None = None
