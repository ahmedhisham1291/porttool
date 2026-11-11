"""The dry run: imported rows -> validation -> write plan. Pure; touches no file."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace

from porttool.core.config import ProjectConfig
from porttool.core.interfaces import ValidationOutcome, Validator, WritePlan, Writer
from porttool.core.models import Model
from porttool.core.operations import ChangeSet, Route
from porttool.core.pending import PendingQueue
from porttool.core.results import (
    Finding,
    RawRequestRow,
    RequestResult,
    RequestStatus,
    RowRef,
    Severity,
)
from porttool.pipeline.pendingio import PendingRead


@dataclass(frozen=True, slots=True)
class DryRun:
    """Result of one dry run, together with what it was computed against."""

    outcome: ValidationOutcome
    plan: WritePlan
    model: Model
    pending_before: PendingQueue  # the queue as read from the pending file
    pending_usable: bool

    @property
    def blocking_findings(self) -> tuple[Finding, ...]:
        """Error findings of the write plan: they refuse the apply."""
        return tuple(f for f in self.plan.findings if f.severity is Severity.ERROR)

    @property
    def pending_changed(self) -> bool:
        return self.outcome.pending != self.pending_before

    @property
    def nothing_to_do(self) -> bool:
        return not self.outcome.change_set.operations and not self.pending_changed


def apply_change_set(change_set: ChangeSet) -> ChangeSet:
    """The part of ``change_set`` that is written into the workspace now."""
    return change_set.model_copy(update={"operations": change_set.routed(Route.APPLY)})


def request_id_of(row: RawRequestRow) -> str:
    """Same format as the validator's request ids: ``<workbook>#<sheet>#<row>``."""
    return f"{row.row.file}#{row.row.sheet}#{row.row.row}"


def run_dry_run(
    model: Model,
    config: ProjectConfig,
    rows: Sequence[RawRequestRow],
    import_findings: Sequence[Finding],
    pending: PendingRead,
    validator: Validator,
    writer: Writer,
    now_utc: str,
) -> DryRun:
    """Validate ``rows`` against the model and the pending queue and plan the write.

    A row whose ``RowRef`` carries an import error is rejected here and never reaches the
    validator (the workbook package returns such rows on purpose).
    """
    by_row: dict[RowRef, list[Finding]] = defaultdict(list)
    general: list[Finding] = list(pending.findings)
    known_rows = {row.row for row in rows}
    for finding in import_findings:
        if finding.row is not None and finding.row in known_rows:
            by_row[finding.row].append(finding)
        else:
            general.append(finding)
    rejected = {
        ref for ref, found in by_row.items() if any(f.severity is Severity.ERROR for f in found)
    }
    good = [row for row in rows if row.row not in rejected]
    outcome = validator.validate(model, config, good, pending.queue, now_utc)
    plan = writer.plan(model, config, apply_change_set(outcome.change_set))

    validated = iter(outcome.results)
    results: list[RequestResult] = []
    for row in rows:
        if row.row in rejected:
            results.append(
                RequestResult(
                    request_id=request_id_of(row),
                    status=RequestStatus.REJECTED,
                    row=row.row,
                    findings=tuple(by_row[row.row]),
                )
            )
            continue
        result = next(validated, None)
        if result is None:  # the validator returned fewer results than rows
            break
        extra = by_row.get(row.row)
        if extra and result.row == row.row:
            result = replace(result, findings=(*extra, *result.findings))
        results.append(result)
    results.extend(validated)
    general.extend(outcome.findings)
    merged = ValidationOutcome(
        change_set=outcome.change_set,
        results=tuple(results),
        pending=outcome.pending,
        findings=tuple(general),
    )
    return DryRun(
        outcome=merged,
        plan=plan,
        model=model,
        pending_before=pending.queue,
        pending_usable=pending.usable,
    )


def all_findings(dry_run: DryRun) -> tuple[Finding, ...]:
    found: list[Finding] = list(dry_run.outcome.findings)
    found.extend(dry_run.plan.findings)
    for result in dry_run.outcome.results:
        found.extend(result.findings)
    return tuple(found)


def count_by_status(results: Sequence[RequestResult]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        counts[result.status.value] = counts.get(result.status.value, 0) + 1
    return counts
