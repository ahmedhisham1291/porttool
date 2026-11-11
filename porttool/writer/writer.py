"""``ArxmlWriter``: the ``Writer`` of porttool.core.interfaces."""

from __future__ import annotations

import logging
import os
import stat
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lxml import etree

from porttool.core import fsutil
from porttool.core.config import ProjectConfig
from porttool.core.errors import JobCancelled, RepoStateError
from porttool.core.interfaces import JobContext, Journal, PlannedFile, WriteOutcome, WritePlan
from porttool.core.models import Model
from porttool.core.operations import AnyOperation, ChangeSet, Route
from porttool.core.results import Finding, Severity
from porttool.writer import codes
from porttool.writer.dcf import register_new_file_in_dcf
from porttool.writer.session import FAILED, PLANNED, Session, op_finding
from porttool.writer.settings import WriterSettings

_logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _Prepared:
    """Result of one computation: new bytes per file, state per operation, findings."""

    session: Session
    outputs: dict[str, bytes] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)


def _summary(notes: Sequence[str]) -> str:
    text = "add " + ", ".join(notes[:6])
    return text if len(notes) <= 6 else f"{text} and {len(notes) - 6} more"


class ArxmlWriter:
    """Edits the workspace ARXML in place by inserting text. See HANDOFF.md.

    ``uuid_factory`` (tests), ``retries`` and ``backoff_s`` (atomic replace) are optional.
    After ``apply`` returned or raised, ``last_outcome`` holds what was written.
    """

    def __init__(
        self,
        *,
        uuid_factory: Callable[[], str] | None = None,
        retries: int = fsutil.REPLACE_RETRIES,
        backoff_s: float = fsutil.REPLACE_BACKOFF_S,
    ) -> None:
        self._uuid_factory = uuid_factory
        self._retries = retries
        self._backoff_s = backoff_s
        self._schema_lock = threading.Lock()
        self._schema: tuple[tuple[str, int, int], Any] | None = None
        self.last_outcome: WriteOutcome | None = None

    # ---- computation shared by plan and apply

    def _prepare(
        self,
        model: Model,
        config: ProjectConfig,
        operations: Sequence[AnyOperation],
        *,
        strict: bool,
        check_cancelled: Callable[[], None] | None = None,
    ) -> _Prepared:
        settings, setting_findings = WriterSettings.from_config(config)
        session = Session(
            model,
            config,
            settings,
            strict=strict,
            uuid_factory=self._uuid_factory,
            check_cancelled=check_cancelled,
        )
        refused: list[Finding] = []
        excluded: dict[str, AnyOperation] = {}
        schema, schema_findings = self._load_schema(config)
        while True:
            session.reset()
            session.run([op for op in operations if op.id not in excluded])
            outputs = {doc.rel_path: doc.to_bytes() for doc in session.changed_docs()}
            bad = self._preflight(session, outputs, schema)
            if not bad:
                break
            refused.extend(bad.values())
            before = len(excluded)
            for state in session.states.values():
                hit = sorted(state.files & bad.keys())
                if state.status == PLANNED and hit:
                    state.status = FAILED
                    excluded[state.op.id] = state.op
                    refused.append(
                        op_finding(
                            state.op,
                            codes.DEPENDS,
                            Severity.ERROR,
                            f"Not applied because {hit[0]} cannot be written.",
                            file=hit[0],
                        )
                    )
            if len(excluded) == before:  # cannot happen: every changed file has an operation
                for rel_path in bad:
                    del outputs[rel_path]
                break
        prepared = _Prepared(session, outputs)
        prepared.findings = [*setting_findings, *schema_findings, *session.findings, *refused]
        return prepared

    def _preflight(
        self, session: Session, outputs: dict[str, bytes], schema: Any
    ) -> dict[str, Finding]:
        """Files that must not be written: not well-formed, not valid, read-only."""
        bad: dict[str, Finding] = {}
        for rel_path, data in outputs.items():
            finding = _check_xml(rel_path, data, schema)
            if finding is None and not session.docs[rel_path].created:
                finding = _check_writable(session.root / rel_path, rel_path)
            if finding is not None:
                bad[rel_path] = finding
        return bad

    def _load_schema(self, config: ProjectConfig) -> tuple[Any, list[Finding]]:
        """The compiled XSD of ``config.xsd_file`` (cached), or None."""
        if config.xsd_file is None:
            return None, []
        path = str(config.xsd_file)
        try:
            info = os.stat(fsutil.long_path(path))
            key = (path, info.st_size, info.st_mtime_ns)
            with self._schema_lock:
                if self._schema is None or self._schema[0] != key:
                    self._schema = (key, etree.XMLSchema(etree.parse(path, _parser())))
                return self._schema[1], []
        except (OSError, etree.Error) as error:
            return None, [
                Finding(
                    codes.XSD_UNUSABLE,
                    Severity.WARNING,
                    f"The schema {path} cannot be used ({error}). Files are only checked "
                    "for well-formed XML.",
                    file=path,
                )
            ]

    # ---- Writer protocol

    def plan(self, model: Model, config: ProjectConfig, change_set: ChangeSet) -> WritePlan:
        """Compute what would change. Reads files, writes nothing."""
        prepared = self._prepare(model, config, change_set.routed(Route.APPLY), strict=False)
        session = prepared.session
        files = tuple(
            PlannedFile(
                rel_path,
                "create" if session.docs[rel_path].created else "modify",
                _summary(session.notes.get(rel_path, [])),
            )
            for rel_path in sorted(prepared.outputs)
        )
        preview = tuple(
            state.preview
            for op in change_set.operations
            if (state := session.states.get(op.id)) is not None and state.status == PLANNED
        )
        return WritePlan(change_set, files, preview, tuple(prepared.findings))

    async def apply(
        self,
        plan: WritePlan,
        model: Model,
        config: ProjectConfig,
        journal: Journal,
        ctx: JobContext,
    ) -> WriteOutcome:
        """Write the planned files. Raises ``RepoStateError`` if a file changed since the load."""
        self.last_outcome = None
        operations = plan.change_set.routed(Route.APPLY)
        prepared = await ctx.run_blocking(
            lambda: self._prepare(
                model, config, operations, strict=True, check_cancelled=ctx.check_cancelled
            )
        )
        session = prepared.session
        unplanned = sorted(set(prepared.outputs) - {item.rel_path for item in plan.files})
        if unplanned:
            raise RepoStateError(
                f"The plan is out of date: {unplanned[0]} would be written but was not planned. "
                "Run the dry run again.",
                detail=", ".join(unplanned),
            )
        root = Path(config.repo_root)
        todo = sorted(prepared.outputs)
        written: list[str] = []
        findings = list(prepared.findings)
        try:
            for index, rel_path in enumerate(todo):
                ctx.check_cancelled()
                ctx.progress(index, len(todo), rel_path)
                created = session.docs[rel_path].created
                journal.record(rel_path, "create" if created else "modify")
                failure = await ctx.run_blocking(
                    lambda rel=rel_path: self._write(  # type: ignore[misc]
                        root, rel, prepared.outputs[rel], session
                    )
                )
                if failure is not None:
                    findings.append(failure)
                    break
                written.append(rel_path)
                if created:
                    findings.extend(register_new_file_in_dcf(config, rel_path))
            ctx.progress(len(written), len(todo), "")
        except BaseException as error:
            self.last_outcome = self._outcome(session, todo, written, findings)
            if isinstance(error, JobCancelled):
                raise JobCancelled(
                    f"The write was cancelled after {len(written)} of {len(todo)} files.",
                    detail="written: " + ", ".join(written),
                ) from error
            raise
        self.last_outcome = self._outcome(session, todo, written, findings)
        return self.last_outcome

    def _outcome(
        self, session: Session, todo: Sequence[str], written: Sequence[str], findings: list[Finding]
    ) -> WriteOutcome:
        done = set(written)
        applied: list[str] = []
        result = list(findings)
        for state in session.states.values():
            if state.status == PLANNED and state.files <= done:
                applied.append(state.op.id)
                result.extend(state.infos)
        missing = [rel_path for rel_path in todo if rel_path not in done]
        if missing:
            result.append(
                Finding(
                    codes.ABORTED,
                    Severity.ERROR,
                    f"The write stopped after {len(written)} of {len(todo)} files. Written: "
                    f"{', '.join(written) or 'none'}. Not written: {', '.join(missing)}.",
                    file=missing[0],
                )
            )
        return WriteOutcome(tuple(written), tuple(applied), tuple(result))

    def _write(self, root: Path, rel_path: str, data: bytes, session: Session) -> Finding | None:
        """Write one file atomically. A refused write is a finding, a changed file an error."""
        path = root / rel_path
        before = session.read_stats.get(rel_path)
        if before is None:
            if os.path.lexists(fsutil.long_path(path)):
                raise RepoStateError(
                    f"{rel_path} appeared while the change was computed. Reload and try again.",
                    detail=rel_path,
                )
        else:
            try:
                now = fsutil.fingerprint(path, root)
            except OSError as error:
                raise RepoStateError(
                    f"{rel_path} cannot be read any more ({error.strerror or error}).",
                    detail=rel_path,
                ) from error
            if (now.size, now.mtime_ns) != (before.size, before.mtime_ns):
                raise RepoStateError(
                    f"{rel_path} changed while the change was computed. Reload and try again.",
                    detail=rel_path,
                )
        try:
            fsutil.atomic_write_bytes(path, data, retries=self._retries, backoff_s=self._backoff_s)
        except fsutil.FileWriteError as error:
            _logger.warning("write refused: %s", error)
            if isinstance(error, fsutil.ReadOnlyFileError):
                code = codes.READ_ONLY
            elif isinstance(error, fsutil.FileLockedError):
                code = codes.LOCKED
            else:
                code = codes.WRITE
            return Finding(code, Severity.ERROR, error.user_message, file=rel_path)
        return None

    def build_delta(
        self, model: Model, config: ProjectConfig, change_set: ChangeSet, target: Path
    ) -> tuple[Finding, ...]:
        raise NotImplementedError


# --------------------------------------------------------------------------- checks


def _parser() -> Any:
    """lxml parser that loads nothing from outside the file."""
    return etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)


def _check_xml(rel_path: str, data: bytes, schema: Any) -> Finding | None:
    """Well-formedness, and validity if a schema is configured, of the bytes to be written."""
    try:
        tree = etree.fromstring(data, _parser())
    except etree.XMLSyntaxError as error:
        return Finding(
            codes.NOT_WELL_FORMED,
            Severity.ERROR,
            f"{rel_path} would not be well-formed XML after the change ({error.msg}). "
            "The file is left as it is.",
            file=rel_path,
        )
    if schema is not None and not schema.validate(tree):
        first = schema.error_log[0] if len(schema.error_log) else None
        detail = f"line {first.line}: {first.message}" if first is not None else "no detail"
        return Finding(
            codes.XSD_INVALID,
            Severity.ERROR,
            f"{rel_path} would not be valid against the AUTOSAR schema after the change "
            f"({detail}). The file is left as it is.",
            file=rel_path,
        )
    return None


def _check_writable(path: Path, rel_path: str) -> Finding | None:
    """Read-only attribute of an existing target, seen before anything is written."""
    try:
        mode = os.stat(fsutil.long_path(path)).st_mode
    except OSError:
        return None  # judged by the fingerprint check of the write
    if not mode & stat.S_IWRITE:
        return Finding(
            codes.READ_ONLY,
            Severity.ERROR,
            f"{rel_path} is read-only. Remove the write protection (or check the file out) "
            "and try again.",
            file=rel_path,
        )
    return None
