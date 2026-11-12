"""Service objects as plain JSON-able dictionaries."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping

from porttool.core.interfaces import ApplyReport, JobView, ValidationOutcome, WritePlan
from porttool.core.results import Finding, RequestResult, RequestStatus, RowRef

ACTIVE_STATES = frozenset({"queued", "running"})
#: Most request results one call returns (the counts always cover every request).
MAX_RESULTS = 5000
_MAX_PREVIEW_LINES = 2000


def row_json(row: RowRef | None, names: Mapping[str, str]) -> dict[str, object] | None:
    if row is None:
        return None
    return {"file": names.get(row.file, row.file), "sheet": row.sheet, "row": row.row}


def finding_json(finding: Finding, names: Mapping[str, str] | None = None) -> dict[str, object]:
    return {
        "code": finding.code,
        "severity": finding.severity.value,
        "message": finding.message,
        "object_path": finding.object_path,
        "file": finding.file,
        "row": row_json(finding.row, names or {}),
    }


def job_json(
    job: JobView, elapsed_s: float | None, log_file: str | None, with_log: bool = True
) -> dict[str, object]:
    data: dict[str, object] = {
        "id": job.id,
        "name": job.name,
        "state": job.state,
        "active": job.state in ACTIVE_STATES,
        "done": job.done,
        "total": job.total,
        "message": job.message,
        "error": job.error,
        "elapsed_s": None if elapsed_s is None else round(elapsed_s, 1),
    }
    if with_log:
        data["log_tail"] = list(job.log_tail)
        data["summary"] = dict(job.summary) if job.summary is not None else None
    if job.state == "failed":
        data["log_file"] = log_file
    return data


def _status_order() -> list[str]:
    return [status.value for status in RequestStatus]


def count_by_status(results: Iterable[RequestResult]) -> dict[str, int]:
    """Number of requests per status, in the order of ``RequestStatus``; empty ones left out."""
    counts = Counter(result.status.value for result in results)
    return {status: counts[status] for status in _status_order() if counts[status]}


def result_json(result: RequestResult, names: Mapping[str, str]) -> dict[str, object]:
    return {
        "request_id": result.request_id,
        "status": result.status.value,
        "row": row_json(result.row, names),
        "reasons": [
            {"code": f.code, "severity": f.severity.value, "message": f.message}
            for f in result.findings
        ],
        "operations": len(result.operation_ids),
    }


def _results_json(
    results: tuple[RequestResult, ...], status: str | None, names: Mapping[str, str]
) -> tuple[list[dict[str, object]], bool]:
    chosen = [r for r in results if status is None or r.status.value == status]
    return [result_json(r, names) for r in chosen[:MAX_RESULTS]], len(chosen) > MAX_RESULTS


def validation_json(
    outcome: ValidationOutcome | None,
    plan: WritePlan | None,
    status: str | None,
    names: Mapping[str, str],
) -> dict[str, object]:
    """The last dry run. ``names`` maps saved upload paths to the names the user chose."""
    if outcome is None:
        return {"present": False}
    results, truncated = _results_json(outcome.results, status, names)
    data: dict[str, object] = {
        "present": True,
        "total": len(outcome.results),
        "counts": count_by_status(outcome.results),
        "status_filter": status,
        "results": results,
        "truncated": truncated,
        "findings": [finding_json(f, names) for f in outcome.findings],
        "pending": len(outcome.pending.entries),
        "operations": len(outcome.change_set.operations),
        "plan": None,
    }
    if plan is not None:
        data["plan"] = {
            "preview": list(plan.preview[:_MAX_PREVIEW_LINES]),
            "preview_truncated": len(plan.preview) > _MAX_PREVIEW_LINES,
            "files": [
                {"rel_path": f.rel_path, "action": f.action, "summary": f.summary}
                for f in plan.files
            ],
            "findings": [finding_json(f, names) for f in plan.findings],
        }
    return data


def report_json(report: ApplyReport | None, names: Mapping[str, str]) -> dict[str, object]:
    if report is None:
        return {"present": False}
    results, truncated = _results_json(report.results, None, names)
    return {
        "present": True,
        "ok": report.ok,
        "branch": report.branch,
        "rolled_back": report.rolled_back,
        "report_file": report.report_file,
        "total": len(report.results),
        "counts": count_by_status(report.results),
        "results": results,
        "truncated": truncated,
        "findings": [finding_json(f, names) for f in report.findings],
    }
