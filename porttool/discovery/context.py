"""Shared state of one discovery run: the name mapper, the findings, the progress sink."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from porttool.core.results import Finding, Severity
from porttool.discovery.names import Names

Progress = Callable[[str], None]


def _silent(_: str) -> None:
    return None


@dataclass(slots=True)
class Context:
    """Passed to every step. Findings are data: a step reports them and carries on."""

    names: Names = field(default_factory=Names)
    progress: Progress = _silent
    findings: list[tuple[str, Finding]] = field(default_factory=list)
    step: str = ""

    def add(
        self,
        code: str,
        severity: Severity,
        message: str,
        *,
        file: str | None = None,
        object_path: str | None = None,
    ) -> None:
        """Record a finding. ``file`` is a real repo-relative path; it is mapped here."""
        shown = self.names.rel_path(file) if file is not None else None
        self.findings.append(
            (self.step, Finding(code, severity, message, object_path=object_path, file=shown))
        )

    def info(self, code: str, message: str, *, file: str | None = None) -> None:
        self.add(code, Severity.INFO, message, file=file)

    def warning(self, code: str, message: str, *, file: str | None = None) -> None:
        self.add(code, Severity.WARNING, message, file=file)

    def error(self, code: str, message: str, *, file: str | None = None) -> None:
        self.add(code, Severity.ERROR, message, file=file)

    def findings_json(self) -> list[dict[str, object]]:
        return [
            {
                "step": step,
                "code": finding.code,
                "severity": finding.severity.value,
                "message": finding.message,
                "file": finding.file,
                "object_path": finding.object_path,
            }
            for step, finding in self.findings
        ]

    def count_by_severity(self) -> dict[str, int]:
        counts = {severity.value: 0 for severity in Severity}
        for _, finding in self.findings:
            counts[finding.severity.value] += 1
        return counts


def top(counter: Mapping[str, int], limit: int | None = None) -> dict[str, int]:
    """Counter as a plain dict, largest count first, ties in name order."""
    ordered = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    if limit is not None:
        ordered = ordered[:limit]
    return dict(ordered)
