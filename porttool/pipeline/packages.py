"""Request packages for model owners: a delta ARXML and a request sheet (CSV) per team."""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from porttool.core.config import ProjectConfig
from porttool.core.fsutil import atomic_write_bytes
from porttool.core.interfaces import ValidationOutcome, Writer
from porttool.core.models import Model
from porttool.core.operations import AnyOperation, ChangeSet, Route
from porttool.core.pending import PendingEntry
from porttool.core.results import Finding, Severity
from porttool.pipeline import codes
from porttool.pipeline.describe import object_text, owner_path

_HEADER = (
    "Operation id",
    "Kind",
    "SWC, composition or package",
    "Object",
    "Details (JSON)",
    "Requester",
    "Comment",
    "Workbook",
    "Sheet",
    "Row",
    "State",
    "Depends on",
)
_BASE_FIELDS = {"id", "op", "requester", "comment", "route", "source_file", "source_sheet",
                "source_row"}  # fmt: skip
UNASSIGNED = "unassigned"


@dataclass(frozen=True, slots=True)
class Package:
    team: str
    folder: str  # absolute
    files: tuple[str, ...]  # file names inside the folder
    operations: int


def _csv_bytes(operations: Sequence[AnyOperation], entries: dict[str, PendingEntry]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(_HEADER)
    for op in operations:
        entry = entries.get(op.id)
        details = op.model_dump(mode="json", exclude=_BASE_FIELDS, exclude_defaults=True)
        writer.writerow(
            [
                op.id,
                op.op,
                owner_path(op),
                object_text(op),
                json.dumps(details, ensure_ascii=False, sort_keys=True),
                op.requester,
                op.comment,
                op.source_file or "",
                op.source_sheet or "",
                "" if op.source_row is None else str(op.source_row),
                entry.state.value if entry is not None else "",
                "; ".join(entry.depends_on) if entry is not None else "",
            ]
        )
    return buffer.getvalue().encode("utf-8-sig")  # the BOM makes Excel read UTF-8


def team_of(op: AnyOperation, config: ProjectConfig, entries: dict[str, PendingEntry]) -> str:
    entry = entries.get(op.id)
    if entry is not None and entry.owner_team:
        return entry.owner_team
    name = owner_path(op).rsplit("/", 1)[-1]
    return config.team_of(name) or UNASSIGNED


def write_packages(
    folder: Path,
    *,
    model: Model,
    config: ProjectConfig,
    outcome: ValidationOutcome,
    writer: Writer,
    before_write: Callable[[Path], None],
) -> tuple[tuple[Package, ...], tuple[Finding, ...]]:
    """Write ``<folder>/model_requests/<team>/`` and ``<folder>/held_requests.csv``. Blocking.

    ``before_write(path)`` is called for every file before it is created (journal).
    The request sheet is the binding part; a delta ARXML that cannot be built is a
    warning, because the sheet already holds every value of the request.
    """
    entries = {entry.operation.id: entry for entry in outcome.pending.entries}
    by_team: dict[str, list[AnyOperation]] = {}
    for op in outcome.change_set.routed(Route.MODEL):
        by_team.setdefault(team_of(op, config, entries), []).append(op)
    packages: list[Package] = []
    findings: list[Finding] = []
    for team in sorted(by_team):
        operations = by_team[team]
        target = folder / "model_requests" / (re.sub(r"[^A-Za-z0-9_.-]+", "_", team) or UNASSIGNED)
        sheet, delta = target / "requests.csv", target / "delta.arxml"
        before_write(sheet)
        before_write(delta)
        target.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(sheet, _csv_bytes(operations, entries))
        files = [sheet.name]
        subset = ChangeSet(
            baseline_commit=outcome.change_set.baseline_commit,
            model_hash=outcome.change_set.model_hash,
            created_utc=outcome.change_set.created_utc,
            operations=tuple(operations),
        )
        problems = [
            f
            for f in writer.build_delta(model, config, subset, delta)
            if f.severity is Severity.ERROR
        ]
        if problems:
            findings.append(
                Finding(
                    code=codes.PKG_DELTA,
                    severity=Severity.WARNING,
                    message=f"The delta ARXML for team {team} could not be built "
                    f"({problems[0].code}: {problems[0].message}). The request sheet "
                    f"{sheet} holds the complete requests.",
                )
            )
        elif delta.is_file():
            files.append(delta.name)
        packages.append(Package(team, str(target), tuple(files), len(operations)))
        findings.append(
            Finding(
                code=codes.PKG_WRITTEN,
                severity=Severity.INFO,
                message=f"Request package for team {team}: {len(operations)} request(s) in "
                f"{target}.",
            )
        )
    held = outcome.change_set.routed(Route.HOLD)
    if held:
        sheet = folder / "held_requests.csv"
        before_write(sheet)
        folder.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(sheet, _csv_bytes(held, entries))
    return tuple(packages), tuple(findings)
