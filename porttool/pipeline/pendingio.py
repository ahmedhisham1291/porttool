"""Reading and rendering the pending queue file (``config.pending_file``)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from porttool.core.config import ProjectConfig
from porttool.core.pending import PendingQueue
from porttool.core.results import Finding, Severity
from porttool.pipeline import codes

_FORMAT_VERSION = 1


@dataclass(frozen=True, slots=True)
class PendingRead:
    queue: PendingQueue
    findings: tuple[Finding, ...]
    exists: bool

    @property
    def usable(self) -> bool:
        return not any(finding.severity is Severity.ERROR for finding in self.findings)


def pending_path(config: ProjectConfig) -> Path:
    return config.repo_root / config.pending_file


def render_pending(queue: PendingQueue) -> str:
    """The exact text stored in the pending file (LF line ends, final newline)."""
    return queue.to_json() + "\n"


def _has_conflict_markers(text: str) -> bool:
    return any(line.startswith(("<<<<<<< ", ">>>>>>> ", "||||||| ")) for line in text.splitlines())


def read_pending(config: ProjectConfig) -> PendingRead:
    """Read the pending queue. Blocking. Never raises for a bad file.

    A missing file is an empty queue. A file that cannot be read, is not a pending queue
    or holds merge-conflict markers gives an error finding and an empty queue: the dry
    run still shows results, the apply is refused.
    """
    path = pending_path(config)
    rel = config.pending_file

    def bad(code: str, message: str) -> PendingRead:
        finding = Finding(code=code, severity=Severity.ERROR, message=message, file=rel)
        return PendingRead(queue=PendingQueue(), findings=(finding,), exists=True)

    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return PendingRead(queue=PendingQueue(), findings=(), exists=False)
    except OSError as error:
        return bad(
            codes.PEND_UNREADABLE,
            f"The pending file {rel} cannot be read: {error.strerror or error}.",
        )
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return bad(codes.PEND_MALFORMED, f"The pending file {rel} is not UTF-8 text.")
    if _has_conflict_markers(text):
        return bad(
            codes.PEND_CONFLICT,
            f"The pending file {rel} holds git merge-conflict markers. Resolve the "
            "conflict, commit, and run the dry run again.",
        )
    if not text.strip():
        return PendingRead(queue=PendingQueue(), findings=(), exists=True)
    try:
        queue = PendingQueue.from_json(text)
    except (ValidationError, ValueError) as error:
        first = str(error).splitlines()[0] if str(error) else type(error).__name__
        return bad(
            codes.PEND_MALFORMED,
            f"The pending file {rel} is not a valid pending queue ({first}). Repair it or "
            "restore it from git, then run the dry run again.",
        )
    if queue.format_version > _FORMAT_VERSION:
        return bad(
            codes.PEND_MALFORMED,
            f"The pending file {rel} has format_version {queue.format_version}; this "
            f"PortTool reads format {_FORMAT_VERSION}. Update PortTool.",
        )
    return PendingRead(queue=queue, findings=(), exists=True)
