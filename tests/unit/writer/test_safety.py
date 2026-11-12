"""Safety rules of plan and apply: state checks, owners, journal, cancel, failures."""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from porttool.core import fsutil
from porttool.core.errors import JobCancelled, RepoStateError
from porttool.core.models import AccessKind, PortDirection, TriggerKind
from porttool.core.operations import (
    AddPort,
    AnyOperation,
    DeleteAccessPoint,
    DeleteConnector,
    DeletePort,
    DeleteTrigger,
    ModifyPort,
)
from porttool.core.results import Severity
from porttool.writer import ArxmlWriter, codes
from porttool.writer.xmltext import Document
from tests.unit.writer.conftest import (
    COMPOSITION,
    CT,
    IF,
    FakeContext,
    Repo,
    added,
    codes_of,
    errors,
)
from tests.unit.writer.test_behavior import access, trigger
from tests.unit.writer.test_ports_connectors import _connector, _port

SWC1, SWC2, SWC3 = f"{CT}/Swc1", f"{CT}/Swc2", f"{CT}/Swc3"
SWC1_FILE = "Config/Developer/ComponentTypes/Swc1.arxml"
SWC2_FILE = "Config/Developer/ComponentTypes/Swc2.arxml"
COMPOSITION_FILE = "Config/Developer/ComponentTypes/EcuComposition.arxml"

#: Three files, written in this order: composition, Swc1, Swc2.
THREE_FILES: tuple[AnyOperation, ...] = (
    _port(SWC1, "PpNew", "P", "IfSig1"),
    _port(SWC2, "RpNew", "R", "IfSig1"),
    _connector("Swc1_PpNew_Swc2_RpNew", "Swc1", "PpNew", "Swc2", "RpNew"),
)


def test_read_only_target(small: Repo) -> None:
    os.chmod(small.path(SWC2_FILE), 0o444)
    before = small.snapshot()
    plan = small.plan(*THREE_FILES)
    # the dry run already says so; the port of Swc1 does not depend on the file and is planned
    assert codes_of(plan.findings) == [codes.READ_ONLY, codes.DEPENDS, codes.REF_MISSING]
    assert plan.findings[0].file == SWC2_FILE and "read-only" in plan.findings[0].message
    assert [f.rel_path for f in plan.files] == [SWC1_FILE]
    outcome = small.apply_plan(plan)
    assert outcome.written == (SWC1_FILE,)
    assert outcome.applied_operation_ids == ("port-1-PpNew",)
    assert codes_of(errors(outcome.findings)) == [
        codes.READ_ONLY,
        codes.DEPENDS,
        codes.REF_MISSING,
    ]
    after = small.snapshot()
    assert after[SWC2_FILE] == before[SWC2_FILE]
    assert after[COMPOSITION_FILE] == before[COMPOSITION_FILE]
    assert not os.access(small.path(SWC2_FILE), os.W_OK) or os.geteuid() == 0
    assert os.stat(small.path(SWC2_FILE)).st_mode & 0o222 == 0  # protection was not removed


def test_locked_target_permission_error_from_the_atomic_write(
    small: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_replace = os.replace
    attempts: list[str] = []

    def replace(source: str, target: str) -> None:
        if target.endswith("Swc1.arxml"):
            attempts.append(target)
            error = PermissionError(13, "The process cannot access the file")
            error.winerror = 32  # type: ignore[attr-defined]
            raise error
        real_replace(source, target)

    monkeypatch.setattr(os, "replace", replace)
    before = small.snapshot()
    outcome = small.apply(*THREE_FILES)
    assert len(attempts) == 1 + fsutil.REPLACE_RETRIES
    # the composition was written before the locked file; the write stopped at the lock
    assert outcome.written == (COMPOSITION_FILE,)
    assert outcome.applied_operation_ids == ("conn-Swc1_PpNew_Swc2_RpNew",)
    assert codes_of(outcome.findings) == [codes.LOCKED, codes.ABORTED]
    locked = outcome.findings[0]
    assert locked.file == SWC1_FILE and "in use by another program" in locked.message
    after = small.snapshot()
    assert after[SWC1_FILE] == before[SWC1_FILE] and after[SWC2_FILE] == before[SWC2_FILE]
    assert set(after) == set(before)  # no temporary file left behind
    assert small.journal.paths == [COMPOSITION_FILE, SWC1_FILE]


def test_file_changed_since_load(small: Repo) -> None:
    path = small.path(SWC2_FILE)
    path.write_bytes(path.read_bytes().replace(b"RpSig1", b"RpSigX"))
    before = small.snapshot()
    plan = small.plan(*THREE_FILES)
    assert codes_of(plan.findings) == [codes.STATE_CHANGED, codes.REF_MISSING]
    assert plan.findings[0].file == SWC2_FILE
    assert [f.rel_path for f in plan.files] == [SWC1_FILE]
    with pytest.raises(RepoStateError) as raised:
        small.apply_plan(plan)
    assert SWC2_FILE in str(raised.value) and raised.value.code == "PT-E410"
    assert small.snapshot() == before and not small.journal.entries


def test_file_changed_since_load_is_seen_without_a_recorded_hash(small: Repo) -> None:
    # same size, other time stamp: only the fingerprint (size, mtime) can tell
    import dataclasses

    from porttool.core.models import Model

    files = [dataclasses.replace(f, sha256=None) for f in small.model.files.values()]
    model = small.model
    small.model = Model(
        components=model.components.values(),
        ports=model.ports.values(),
        interfaces=model.interfaces.values(),
        files=files,
    )
    assert not small.plan(_port(SWC1, "PpNew", "P", "IfSig1")).findings
    path = small.path(SWC1_FILE)
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    plan = small.plan(_port(SWC1, "PpNew", "P", "IfSig1"))
    assert codes_of(plan.findings) == [codes.STATE_CHANGED]


def test_file_deleted_or_unknown_since_load(small: Repo) -> None:
    small.path(SWC1_FILE).unlink()
    plan = small.plan(_port(SWC1, "PpNew", "P", "IfSig1"))
    assert codes_of(plan.findings) == [codes.STATE_UNKNOWN]
    with pytest.raises(RepoStateError):
        small.apply_plan(plan)


def test_apply_refuses_a_plan_that_does_not_match(small: Repo) -> None:
    plan = small.plan(_port(SWC1, "PpNew", "P", "IfSig1"))
    stale = type(plan)(small.plan(*THREE_FILES).change_set, plan.files, plan.preview)
    with pytest.raises(RepoStateError, match="plan is out of date"):
        small.apply_plan(stale)
    assert not small.journal.entries


def test_model_owned_and_locked_components_are_never_written(small: Repo) -> None:
    locked = small.path("locked.txt")
    locked.write_text(locked.read_text() + "Swc2\n")
    small.reload()
    before = small.snapshot()
    operations = (
        _port(SWC3, "PpNew", "P", "IfSig1"),
        access(SWC3, "Swc3_Cyclic10ms", "RpSig1", "De1", AccessKind.IMPLICIT_READ),
        trigger(SWC3, "Swc3_Cyclic10ms", TriggerKind.INIT),
        _port(SWC2, "RpNew", "R", "IfSig1"),
    )
    outcome = small.apply(*operations)
    assert codes_of(outcome.findings) == [codes.OWNER] * 4
    assert all(f.severity is Severity.ERROR for f in outcome.findings)
    assert "model-owned" in outcome.findings[0].message
    assert "locked" in outcome.findings[1].message
    assert outcome.written == () and outcome.applied_operation_ids == ()
    assert small.snapshot() == before and not small.journal.entries


def test_modify_and_delete_are_not_supported_yet(small: Repo) -> None:
    plan = small.plan(
        ModifyPort(id="m", swc_path=SWC1, name="PpSig1", interface_path=f"{IF}/IfSig3"),
        DeletePort(id="d1", swc_path=SWC1, name="PpSig1"),
        DeleteConnector(id="d2", composition_path=COMPOSITION, name="Swc1_PpSig1_Swc2_RpSig1"),
        DeleteAccessPoint(
            id="d3",
            swc_path=SWC1,
            runnable_name="Swc1_Cyclic10ms",
            port_name="PpSig3",
            target="De1",
            kind=AccessKind.IMPLICIT_WRITE,
        ),
        DeleteTrigger(id="d4", swc_path=SWC1, name="IT_Swc1_Init"),
    )
    assert codes_of(plan.findings) == [codes.UNSUPPORTED] * 5
    assert all("not supported yet" in f.message for f in plan.findings)
    assert not plan.files and not plan.preview


def test_journal_is_recorded_before_each_write_and_work_runs_in_the_pool(
    small: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []
    work_threads: set[int] = set()
    real_write = fsutil.atomic_write_bytes
    real_index = Document.from_bytes.__func__  # type: ignore[attr-defined]

    def write(path: object, data: bytes, **more: object) -> None:
        order.append(f"write {Path(str(path)).name}")
        work_threads.add(threading.get_ident())
        real_write(path, data, **more)  # type: ignore[arg-type]

    def index(cls: type[Document], rel_path: str, data: bytes) -> Document:
        work_threads.add(threading.get_ident())
        return real_index(cls, rel_path, data)  # type: ignore[no-any-return]

    class Journal:
        def record(self, rel_path: str, action: str) -> None:
            order.append(f"journal {Path(rel_path).name} {action}")

    monkeypatch.setattr(fsutil, "atomic_write_bytes", write)
    monkeypatch.setattr(Document, "from_bytes", classmethod(index))
    plan = small.plan(*THREE_FILES)
    work_threads.clear()
    small.journal = Journal()  # type: ignore[assignment]
    context = FakeContext()
    small.apply_plan(plan, context)
    assert order == [
        "journal EcuComposition.arxml modify",
        "write EcuComposition.arxml",
        "journal Swc1.arxml modify",
        "write Swc1.arxml",
        "journal Swc2.arxml modify",
        "write Swc2.arxml",
    ]
    assert context.blocking_calls == 4  # one computation, three writes
    assert work_threads and context.loop_thread not in work_threads
    assert context.events[0] == (0, 3, COMPOSITION_FILE) and context.events[-1] == (3, 3, "")


def test_cancel_between_two_files(small: Repo) -> None:
    before = small.snapshot()
    plan = small.plan(*THREE_FILES)
    context = FakeContext(cancel_at_progress=1)  # cancelled while the first file is written
    with pytest.raises(JobCancelled) as raised:
        small.apply_plan(plan, context)
    assert "after 1 of 3 files" in str(raised.value)
    assert raised.value.detail == f"written: {COMPOSITION_FILE}"
    # the journal is complete: exactly the file that was touched
    assert small.journal.entries == [(COMPOSITION_FILE, "modify")]
    after = small.snapshot()
    assert {name for name in after if after[name] != before[name]} == {COMPOSITION_FILE}
    outcome = small.writer.last_outcome
    assert outcome is not None
    assert outcome.written == (COMPOSITION_FILE,)
    assert outcome.applied_operation_ids == ("conn-Swc1_PpNew_Swc2_RpNew",)
    assert codes_of(outcome.findings) == [codes.ABORTED]
    assert f"Not written: {SWC1_FILE}, {SWC2_FILE}" in outcome.findings[0].message


def test_cancel_before_anything_is_written(small: Repo) -> None:
    plan = small.plan(*THREE_FILES)
    context = FakeContext()
    context.cancel()
    before = small.snapshot()
    with pytest.raises(JobCancelled):
        small.apply_plan(plan, context)
    assert small.snapshot() == before and not small.journal.entries


def test_failure_after_some_files_are_written(
    small: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_write = fsutil.atomic_write_bytes

    def write(path: object, data: bytes, **more: object) -> None:
        if str(path).endswith("Swc2.arxml"):
            raise fsutil.FileWriteError("The disk is full.", path=Path(str(path)))
        real_write(path, data, **more)  # type: ignore[arg-type]

    monkeypatch.setattr(fsutil, "atomic_write_bytes", write)
    before = small.snapshot()
    outcome = small.apply(*THREE_FILES)
    assert outcome.written == (COMPOSITION_FILE, SWC1_FILE)
    assert set(outcome.applied_operation_ids) == {"conn-Swc1_PpNew_Swc2_RpNew", "port-1-PpNew"}
    assert codes_of(outcome.findings) == [codes.WRITE, codes.ABORTED]
    assert outcome.findings[0].file == SWC2_FILE
    assert f"Written: {COMPOSITION_FILE}, {SWC1_FILE}. Not written: {SWC2_FILE}" in (
        outcome.findings[1].message
    )
    # the journal names every file that was or might have been touched
    assert small.journal.paths == [COMPOSITION_FILE, SWC1_FILE, SWC2_FILE]
    assert small.snapshot()[SWC2_FILE] == before[SWC2_FILE]


def test_unexpected_error_after_some_files_are_written_keeps_the_outcome(
    small: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_write = fsutil.atomic_write_bytes

    def write(path: object, data: bytes, **more: object) -> None:
        if str(path).endswith("Swc1.arxml"):
            raise RuntimeError("boom")
        real_write(path, data, **more)  # type: ignore[arg-type]

    monkeypatch.setattr(fsutil, "atomic_write_bytes", write)
    with pytest.raises(RuntimeError):
        small.apply(*THREE_FILES)
    outcome = small.writer.last_outcome
    assert outcome is not None and outcome.written == (COMPOSITION_FILE,)
    assert small.journal.paths == [COMPOSITION_FILE, SWC1_FILE]


def test_file_changed_between_computation_and_write(
    small: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_write = fsutil.atomic_write_bytes
    target = small.path(SWC2_FILE)

    def write(path: object, data: bytes, **more: object) -> None:
        real_write(path, data, **more)  # type: ignore[arg-type]
        if str(path).endswith("Swc1.arxml"):  # somebody saves Swc2 while Swc1 is written
            target.write_bytes(target.read_bytes() + b"\n")

    monkeypatch.setattr(fsutil, "atomic_write_bytes", write)
    with pytest.raises(RepoStateError, match="Swc2.arxml changed while"):
        small.apply(*THREE_FILES)
    assert small.writer.last_outcome is not None
    assert small.writer.last_outcome.written == (COMPOSITION_FILE, SWC1_FILE)


def test_applying_twice(small: Repo) -> None:
    from tests.unit.writer.test_small_one_of_each import ONE_OF_EACH

    first = small.apply(*ONE_OF_EACH)
    assert len(first.applied_operation_ids) == len(ONE_OF_EACH)
    # without a reload the files no longer match the model
    with pytest.raises(RepoStateError):
        small.apply(*ONE_OF_EACH)
    model = small.reload()
    after_first = small.snapshot()
    entries = list(small.journal.entries)

    plan = small.plan(*ONE_OF_EACH)
    assert not plan.files and not plan.preview
    assert codes_of(plan.findings) == [codes.INFO_EXISTS] * len(ONE_OF_EACH)
    second = small.apply_plan(plan)
    assert second.written == () and second.applied_operation_ids == ()
    assert codes_of(second.findings) == [codes.INFO_EXISTS] * len(ONE_OF_EACH)
    assert all(f.severity is Severity.INFO for f in second.findings)
    assert small.snapshot() == after_first and small.journal.entries == entries
    assert added(model, small.load()) == set()


def test_same_request_twice_in_one_change_set_is_written_once(small: Repo) -> None:
    again = _port(SWC1, "PpNew", "P", "IfSig1").model_copy(update={"id": "again"})
    read = access(SWC2, "Swc2_Cyclic10ms", "RpSig1", "De1", AccessKind.IMPLICIT_READ)
    init = trigger(SWC2, "Swc2_Cyclic10ms", TriggerKind.INIT)
    outcome = small.apply(
        _port(SWC1, "PpNew", "P", "IfSig1"),
        again,
        read,
        read.model_copy(update={"id": "read-again"}),
        init,
        init.model_copy(update={"id": "init-again"}),
    )
    assert codes_of(outcome.findings).count(codes.INFO_EXISTS) == 3
    assert len(outcome.applied_operation_ids) == 3
    assert small.read(SWC1_FILE).count(b"<SHORT-NAME>PpNew</SHORT-NAME>") == 1


def test_name_already_used(small: Repo) -> None:
    plan = small.plan(
        _port(SWC1, "PpSig1", "P", "IfSig3"),  # a port with another interface
        _port(SWC1, "RpSrv2", "P", "IfSrv2"),  # a port with another direction
        _port(SWC1, "Swc1_InternalBehavior", "P", "IfSig1"),  # the behavior has that name
        _connector("Swc1", "Swc1", "PpSig1", "Swc2", "RpSig1"),  # a prototype has that name
        _connector("Swc1_PpSig1_Swc2_RpSig1", "Swc1", "PpSig3", "Swc2", "RpSig3"),  # other ends
    )
    assert codes_of(plan.findings) == [codes.NAME_USED] * 5
    assert "already" in plan.findings[0].message
    assert not plan.files


def test_well_formedness_is_checked_before_a_file_is_replaced(
    small: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    from porttool.writer import templates

    real = templates.named

    def broken(tag: str, name: str, *children: object, uuid: str | None = None) -> object:
        return real("BAD TAG" if tag == "R-PORT-PROTOTYPE" else tag, name, *children, uuid=uuid)  # type: ignore[arg-type]

    monkeypatch.setattr(templates, "named", broken)
    before = small.snapshot()
    outcome = small.apply(*THREE_FILES)
    assert codes_of(outcome.findings) == [codes.NOT_WELL_FORMED, codes.DEPENDS, codes.REF_MISSING]
    assert outcome.findings[0].file == SWC2_FILE
    assert outcome.written == (SWC1_FILE,)
    assert small.snapshot()[SWC2_FILE] == before[SWC2_FILE]


_LAX_XSD = """<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"
           xmlns:AR="http://autosar.org/schema/r4.0"
           targetNamespace="http://autosar.org/schema/r4.0" elementFormDefault="qualified">
  <xs:element name="AUTOSAR">
    <xs:complexType>
      <xs:sequence>
        <xs:any processContents="lax" minOccurs="0" maxOccurs="unbounded"/>
      </xs:sequence>
      <xs:anyAttribute processContents="lax"/>
    </xs:complexType>
  </xs:element>
  <!-- stands for "the schema does not allow this": a PR port needs an attribute nobody writes -->
  <xs:element name="PR-PORT-PROTOTYPE">
    <xs:complexType>
      <xs:sequence>
        <xs:any processContents="lax" minOccurs="0" maxOccurs="unbounded"/>
      </xs:sequence>
      <xs:attribute name="UUID"/>
      <xs:attribute name="REQUIRED-BY-TEST" use="required"/>
    </xs:complexType>
  </xs:element>
</xs:schema>
"""


def test_schema_error_aborts_that_file_with_a_finding(small: Repo) -> None:
    small.xsd = small.root.parent / "mini.xsd"
    small.xsd.write_text(_LAX_XSD, encoding="utf-8")
    before = small.snapshot()
    outcome = small.apply(
        _port(SWC1, "PpNew", "P", "IfSig1"),  # valid for the schema
        _port(SWC2, "PrpNew", "PR", "IfSig1"),  # not valid
    )
    assert codes_of(outcome.findings) == [codes.XSD_INVALID, codes.DEPENDS]
    invalid = outcome.findings[0]
    assert invalid.file == SWC2_FILE and "REQUIRED-BY-TEST" in invalid.message
    assert outcome.written == (SWC1_FILE,) and outcome.applied_operation_ids == ("port-1-PpNew",)
    assert small.snapshot()[SWC2_FILE] == before[SWC2_FILE]
    assert small.journal.paths == [SWC1_FILE]


def test_unusable_schema_is_reported_and_files_are_still_checked(small: Repo) -> None:
    small.xsd = small.root.parent / "broken.xsd"
    small.xsd.write_text("<xs:schema", encoding="utf-8")
    outcome = small.apply(_port(SWC1, "PpNew", "P", "IfSig1"))
    assert codes_of(outcome.findings) == [codes.XSD_UNUSABLE]
    assert outcome.findings[0].severity is Severity.WARNING
    assert outcome.written == (SWC1_FILE,)
    small.xsd = small.root.parent / "missing.xsd"
    small.reload()
    assert codes_of(small.plan(_port(SWC1, "PpOther", "P", "IfSig1")).findings) == [
        codes.XSD_UNUSABLE
    ]


def test_writer_has_no_required_constructor_arguments_and_follows_the_protocol() -> None:
    from porttool.core.interfaces import Writer

    writer: Writer = ArxmlWriter()
    assert writer is not None
    assert PortDirection.P.value == "P"
    assert isinstance(AddPort.model_fields["swc_path"].annotation, type)
