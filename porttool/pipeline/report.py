"""The HTML report of an apply: one self-contained file, standard library only.

No template engine and no external resource (no CDN, no script, no image). Every value
that comes from the repository, a workbook or a tool goes through ``html.escape``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from html import escape

from porttool.core.operations import AddServerRunnable, AnyOperation
from porttool.core.results import Finding, RequestResult, Severity
from porttool.pipeline.describe import operation_text
from porttool.pipeline.packages import Package


@dataclass(frozen=True, slots=True)
class StepTiming:
    name: str
    state: str  # "ok", "failed", "cancelled", "skipped"
    seconds: float = 0.0
    note: str = ""


@dataclass(frozen=True, slots=True)
class ReportData:
    ok: bool
    rolled_back: bool
    repo_root: str
    created: str
    tool_version: str
    original_branch: str | None
    original_commit: str | None
    branch: str | None
    commit: str | None
    results: tuple[RequestResult, ...] = ()
    operations: tuple[AnyOperation, ...] = ()
    applied_ids: frozenset[str] = frozenset()
    findings: tuple[Finding, ...] = ()
    timings: tuple[StepTiming, ...] = ()
    skipped_steps: tuple[str, ...] = ()
    enable_hint: str = ""
    trigger_mappings: tuple[Mapping[str, object], ...] = ()
    mapping_done: bool = False
    manual_instructions: tuple[str, ...] = ()
    tool_logs: tuple[tuple[str, str], ...] = ()
    diff_stat: str = ""
    changed_files: tuple[str, ...] = ()
    generated_files: tuple[str, ...] = ()
    packages: tuple[Package, ...] = ()
    failure: str | None = None


_CSS = """
body{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#1b1b1b;background:#fff}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 8px;border-bottom:1px solid #ccc;
padding-bottom:4px}table{border-collapse:collapse;width:100%;font-size:13px}
th,td{border:1px solid #d0d0d0;padding:4px 8px;text-align:left;vertical-align:top}
th{background:#f0f0f0}pre{background:#f6f6f6;border:1px solid #ddd;padding:8px;font-size:12px;
overflow-x:auto;white-space:pre-wrap}.ok{color:#0a6b0a}.bad{color:#b00020}.warn{color:#8a5a00}
.badge{display:inline-block;padding:2px 10px;border-radius:10px;color:#fff;font-weight:600}
.badge.ok{background:#0a6b0a}.badge.bad{background:#b00020}.muted{color:#666}
ul{margin:4px 0 4px 20px;padding:0}
"""
_SEVERITY_CLASS = {Severity.ERROR: "bad", Severity.WARNING: "warn", Severity.INFO: "muted"}
_STATE_CLASS = {"ok": "ok", "skipped": "muted"}
_MAX_ROWS = 2000


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]], empty: str = "None.") -> str:
    """An HTML table. ``rows`` hold HTML that is already escaped."""
    if not rows:
        return f'<p class="muted">{escape(empty)}</p>'
    head = "".join(f"<th>{escape(header)}</th>" for header in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows[:_MAX_ROWS]
    )
    more = (
        f'<p class="muted">{len(rows) - _MAX_ROWS} more rows are not shown.</p>'
        if len(rows) > _MAX_ROWS
        else ""
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>{more}"


def _finding_html(finding: Finding) -> str:
    css = _SEVERITY_CLASS[finding.severity]
    return (
        f'<span class="{css}">{escape(finding.severity.value.upper())} {escape(finding.code)}'
        f"</span>: {escape(finding.message)}"
    )


def _list(items: Sequence[str], empty: str = "None.") -> str:
    if not items:
        return f'<p class="muted">{escape(empty)}</p>'
    shown = "".join(f"<li>{escape(item)}</li>" for item in items[:_MAX_ROWS])
    more = f"<li>... and {len(items) - _MAX_ROWS} more</li>" if len(items) > _MAX_ROWS else ""
    return f"<ul>{shown}{more}</ul>"


def _status_counts(results: Sequence[RequestResult]) -> str:
    counts: dict[str, int] = {}
    for result in results:
        counts[result.status.value] = counts.get(result.status.value, 0) + 1
    return ", ".join(f"{count} {status}" for status, count in sorted(counts.items())) or "none"


def render_report(data: ReportData) -> str:
    """Build the report as one HTML document."""
    by_id = {op.id: op for op in data.operations}
    if data.ok:
        verdict = '<span class="badge ok">APPLIED</span>'
    elif data.rolled_back:
        verdict = '<span class="badge bad">FAILED - ROLLED BACK</span>'
    else:
        verdict = '<span class="badge bad">NOT APPLIED</span>'
    summary_rows = [
        ("Repository", escape(data.repo_root)),
        ("Date", escape(data.created)),
        ("PortTool version", escape(data.tool_version)),
        (
            "Started from",
            escape(
                f"{data.original_branch or 'detached HEAD'} at {(data.original_commit or '?')[:12]}"
            ),
        ),
        ("Branch", escape(data.branch or "none (deleted or never created)")),
        ("Commit", escape(data.commit or "none")),
        ("Requests", escape(_status_counts(data.results))),
    ]
    if data.failure:
        summary_rows.append(("Failure", f'<span class="bad">{escape(data.failure)}</span>'))
    if data.skipped_steps:
        summary_rows.append(
            (
                "DaVinci steps skipped",
                f'<span class="warn">{escape(", ".join(data.skipped_steps))}</span><br>'
                + escape(data.enable_hint),
            )
        )
    parts = [
        "<!DOCTYPE html>",
        '<html lang="en"><head><meta charset="utf-8">',
        f"<title>PortTool apply report - {escape(data.branch or 'not applied')}</title>",
        f"<style>{_CSS}</style></head><body>",
        f"<h1>PortTool apply report {verdict}</h1>",
        "<h2>Summary</h2>",
        _table(("Item", "Value"), [(escape(k), v) for k, v in summary_rows]),
    ]

    request_rows = []
    for result in data.results:
        where = f"{result.row.file}, {result.row.sheet}, row {result.row.row}" if result.row else ""
        operations = "<br>".join(
            escape(operation_text(by_id[i])) if i in by_id else escape(i)
            for i in result.operation_ids
        )
        reasons = "<br>".join(_finding_html(f) for f in result.findings)
        request_rows.append(
            (escape(where or result.request_id), escape(result.status.value), operations, reasons)
        )
    parts += [
        "<h2>Requests</h2>",
        _table(("Request", "Status", "Operations", "Reasons"), request_rows, "No requests."),
    ]

    runnables = [
        (
            escape(op.swc_path),
            escape(op.name),
            escape(op.symbol),
            escape(op.port_name),
            escape(op.operation),
        )  # fmt: skip
        for op in data.operations
        if isinstance(op, AddServerRunnable) and op.id in data.applied_ids
    ]
    parts += [
        "<h2>Created runnables</h2>",
        _table(("SWC", "Runnable", "Symbol", "Port", "Operation"), runnables),
    ]

    mapping_rows = [
        tuple(
            escape(str(item.get(key) or ""))
            for key in (
                "swc_path",
                "runnable",
                "event_name",
                "trigger_kind",
                "task",
                "position_after_event",
            )  # fmt: skip
        )
        for item in data.trigger_mappings
    ]
    parts.append("<h2>Triggers that need a task mapping</h2>")
    if data.trigger_mappings and data.mapping_done:
        parts.append('<p class="ok">The script task mapped these events.</p>')
    parts.append(
        _table(("SWC", "Runnable", "Event", "Trigger", "Task", "After event"), mapping_rows)
    )
    if data.manual_instructions:
        parts.append("<p>Map them by hand:</p>")
        parts.append("<ol>" + "".join(f"<li>{escape(x)}</li>" for x in data.manual_instructions))
        parts.append("</ol>")

    parts += [
        "<h2>Findings</h2>",
        _table(
            ("Severity", "Code", "Message", "Object / file"),
            [
                (
                    f'<span class="{_SEVERITY_CLASS[f.severity]}">'
                    f"{escape(f.severity.value)}</span>",
                    escape(f.code),
                    escape(f.message),
                    escape(f.object_path or f.file or ""),
                )
                for f in data.findings
            ],
            "No findings.",
        ),
        "<h2>Request packages for model owners</h2>",
        _table(
            ("Team", "Requests", "Folder", "Files"),
            [
                (escape(p.team), str(p.operations), escape(p.folder), escape(", ".join(p.files)))
                for p in data.packages
            ],
        ),
        "<h2>Step timings</h2>",
        _table(
            ("Step", "Result", "Seconds", "Note"),
            [
                (
                    escape(t.name),
                    f'<span class="{_STATE_CLASS.get(t.state, "bad")}">{escape(t.state)}</span>',
                    f"{t.seconds:.2f}",
                    escape(t.note),
                )
                for t in data.timings
            ],
        ),
        "<h2>Tool log excerpts</h2>",
    ]
    if data.tool_logs:
        for title, text in data.tool_logs:
            parts.append(f"<h3>{escape(title)}</h3><pre>{escape(text)}</pre>")
    else:
        parts.append('<p class="muted">No DaVinci tool ran.</p>')
    parts += [
        "<h2>git diff --stat</h2>",
        f"<pre>{escape(data.diff_stat)}</pre>" if data.diff_stat else '<p class="muted">None.</p>',
        "<h2>Changed files</h2>",
        _list(data.changed_files),
        "<h2>Changed generated files</h2>",
        _list(data.generated_files, "None (generation did not run or changed nothing)."),
        "</body></html>",
    ]
    return "\n".join(parts) + "\n"
