"""File-level corner cases: broken, odd and moving files must never stop a load."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

import pytest

from porttool.core.errors import JobCancelled
from porttool.core.models import Model
from porttool.core.results import Severity
from porttool.reader import codes, parse
from porttool.reader import reader as reader_module
from porttool.reader.discovery import Discovery
from porttool.reader.discovery import discover as real_discover
from tests.unit.reader import samples
from tests.unit.reader.conftest import Repo, codes_of, findings_with
from tests.unit.reader.samples import CT, IF, doc, package, swc

BROKEN = "Config/Developer/ComponentTypes/Broken.arxml"

UNIT_FILE = doc(
    package(
        "Units",
        "<UNIT><SHORT-NAME>DegC</SHORT-NAME><DISPLAY-NAME>°C</DISPLAY-NAME></UNIT>",
    )
)


def assert_sample_intact(model: Model) -> None:
    """The reference repository is completely there, whatever else happened."""
    assert len(model.components) == 12
    assert len(model.ports) == 14
    assert len(model.connectors) == 6
    assert len(model.interfaces) == 8


def test_malformed_xml(any_repo: Repo) -> None:
    samples.write_sample_repo(any_repo.root, ecuc=False)
    any_repo.write(BROKEN, doc("<AR-PACKAGE><SHORT-NAME>X</SHORT-NAME></AR-PACKAGES>"))
    model = any_repo.load()
    assert_sample_intact(model)
    assert codes_of(model) == [codes.PARSE_MALFORMED]
    finding = model.findings[0]
    assert finding.severity is Severity.ERROR and finding.file == BROKEN
    assert "line 4" in finding.message
    # the broken file is fingerprinted, so the model is not stale because of it
    assert BROKEN in model.files
    assert any_repo.reader.is_stale(any_repo.config(), model) is False


def test_truncated_file(any_repo: Repo) -> None:
    samples.write_sample_repo(any_repo.root, ecuc=False)
    cut = samples.SWC_A[: len(samples.SWC_A) // 2]
    any_repo.write(samples.F_SWC_A, cut)
    model = any_repo.load()
    assert findings_with(model, codes.PARSE_MALFORMED)[0].file == samples.F_SWC_A
    # nothing of the half-read file is in the model
    assert f"{CT}/SwcA" not in model.components
    assert model.ports_of(f"{CT}/SwcA") == ()
    assert f"{CT}/SwcB" in model.components
    # the references into the missing file are reported, not raised
    assert codes.REF_UNRESOLVED in codes_of(model)


def test_empty_file(any_repo: Repo) -> None:
    samples.write_sample_repo(any_repo.root, ecuc=False)
    any_repo.write(BROKEN, b"")
    model = any_repo.load()
    assert_sample_intact(model)
    assert codes_of(model) == [codes.PARSE_EMPTY]
    assert model.findings[0].file == BROKEN and model.findings[0].severity is Severity.ERROR


def test_whitespace_only_file_is_malformed(any_repo: Repo) -> None:
    any_repo.write(BROKEN, b"  \r\n")
    assert codes_of(any_repo.load()) == [codes.PARSE_MALFORMED]


def test_wrong_schema_autosar_3(any_repo: Repo) -> None:
    samples.write_sample_repo(any_repo.root, ecuc=False)
    old = doc(
        package("ComponentTypes", swc("OldSwc")),
        namespace="http://autosar.org/3.2.2",
        schema="AUTOSAR_3-2-2.xsd",
    )
    any_repo.write(BROKEN, old)
    model = any_repo.load()
    assert_sample_intact(model)
    assert codes_of(model) == [codes.SCHEMA_UNSUPPORTED]
    assert model.findings[0].file == BROKEN and "3.2.2" in model.findings[0].message
    assert model.components_named("OldSwc") == ()


def test_wrong_schema_not_autosar_at_all(any_repo: Repo) -> None:
    any_repo.write(BROKEN, '<?xml version="1.0"?><project><name>x</name></project>')
    model = any_repo.load()
    assert codes_of(model) == [codes.SCHEMA_UNSUPPORTED]


def test_mixed_schema_versions(any_repo: Repo) -> None:
    samples.write_sample_repo(any_repo.root, ecuc=False)
    other = "Config/Developer/ComponentTypes/Newer.arxml"
    any_repo.write(other, doc(package("ComponentTypes", swc("Newer")), schema="AUTOSAR_00046.xsd"))
    model = any_repo.load()
    # both versions are loaded; the mix is a warning
    assert f"{CT}/Newer" in model.components and f"{CT}/SwcA" in model.components
    assert codes_of(model) == [codes.SCHEMA_MIXED]
    finding = model.findings[0]
    assert finding.severity is Severity.WARNING and finding.file == other
    assert "AUTOSAR_4-2-2.xsd (7 files)" in finding.message
    assert "AUTOSAR_00046.xsd (1 files)" in finding.message
    stats = any_repo.reader.last_stats
    assert stats is not None and stats.schemas == ("AUTOSAR_00046.xsd", "AUTOSAR_4-2-2.xsd")


@pytest.mark.parametrize(
    ("label", "encode"),
    [
        ("utf-8 without BOM", lambda text: text.encode("utf-8")),
        ("utf-8 with BOM", lambda text: b"\xef\xbb\xbf" + text.encode("utf-8")),
        (
            "utf-16 with BOM",
            lambda text: text.replace('encoding="UTF-8"', 'encoding="UTF-16"').encode("utf-16"),
        ),
        (
            "utf-16 big endian",
            lambda text: (
                b"\xfe\xff"
                + text.replace('encoding="UTF-8"', 'encoding="UTF-16"').encode("utf-16-be")
            ),
        ),
    ],
)
def test_encodings_utf8_bom_and_utf16(any_repo: Repo, label: str, encode: Any) -> None:
    any_repo.write("Config/Developer/Units.arxml", encode(UNIT_FILE))
    model = any_repo.load()
    assert model.findings == (), label
    assert model.units["/Units/DegC"].display_name == "°C"


def test_variation_point_in_utf16_file_is_still_reported(any_repo: Repo) -> None:
    text = doc(
        package(
            "ComponentTypes",
            swc("Variant", extra="<VARIATION-POINT><SHORT-LABEL>v</SHORT-LABEL></VARIATION-POINT>"),
        )
    ).replace('encoding="UTF-8"', 'encoding="UTF-16"')
    any_repo.write("Config/Developer/Variant.arxml", text.encode("utf-16"))
    assert codes_of(any_repo.load()) == [codes.VARIATION_POINT]


def test_crlf_and_lf_line_endings_give_the_same_model(any_repo: Repo, tmp_path: Path) -> None:
    samples.write_sample_repo(any_repo.root)
    lf = any_repo.load(ecuc_file=any_repo.path(samples.F_ECUC))
    for rel in [*samples.SAMPLE_FILES, samples.F_ECUC]:
        target = any_repo.path(rel)
        target.write_bytes(target.read_bytes().replace(b"\n", b"\r\n"))
    crlf = any_repo.load(ecuc_file=any_repo.path(samples.F_ECUC))
    assert crlf.findings == ()
    # same objects, same attributes, same line numbers
    assert crlf.components == lf.components
    assert crlf.ports == lf.ports
    assert crlf.access_points == lf.access_points
    assert crlf.triggers == lf.triggers
    assert crlf.connectors == lf.connectors
    assert crlf.task_mappings == lf.task_mappings
    assert crlf.model_hash != lf.model_hash  # the files did change


def test_xml_comments_and_processing_instructions(any_repo: Repo) -> None:
    text = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<!-- file header comment -->\n"
        "<?davinci keep-this?>\n"
        f'<AUTOSAR xmlns="{samples.NS}"><!-- c --><AR-PACKAGES><?pi in-packages?>'
        "<AR-PACKAGE><!-- before the name --><SHORT-NAME>Comp<!-- inside -->onentTypes"
        "</SHORT-NAME><ELEMENTS><!-- first child --><?pi x?>"
        "<APPLICATION-SW-COMPONENT-TYPE><SHORT-NAME>SwcC</SHORT-NAME><PORTS><!-- c -->"
        "<P-PORT-PROTOTYPE><?pi y?><SHORT-NAME>Pp<!--x-->One</SHORT-NAME><!-- c -->"
        '<PROVIDED-INTERFACE-TREF DEST="SENDER-RECEIVER-INTERFACE"><!-- c -->'
        f"{IF}/IfOne</PROVIDED-INTERFACE-TREF></P-PORT-PROTOTYPE><!-- last --></PORTS>"
        "</APPLICATION-SW-COMPONENT-TYPE><!-- between -->"
        "<SENDER-RECEIVER-INTERFACE><SHORT-NAME>Ignored</SHORT-NAME></SENDER-RECEIVER-INTERFACE>"
        "</ELEMENTS></AR-PACKAGE></AR-PACKAGES></AUTOSAR><!-- trailer -->\n"
    )
    any_repo.write("Config/Developer/Commented.arxml", text)
    any_repo.write(
        "Config/Developer/If.arxml", doc(package("PortInterfaces", samples.sr_interface("IfOne")))
    )
    model = any_repo.load()
    assert codes_of(model) == [codes.REF_UNRESOLVED]  # only IfOne's data type is missing
    port = model.find_port(f"{CT}/SwcC", "PpOne")
    assert port is not None and port.interface_path == f"{IF}/IfOne"
    assert f"{CT}/Ignored" in model.interfaces


def test_doctype_with_entities_is_reported_and_not_expanded(any_repo: Repo) -> None:
    text = doc(package("ComponentTypes", swc("Swc&suffix;"))).replace(
        "<AUTOSAR ", '<!DOCTYPE AUTOSAR [<!ENTITY suffix "Boom">]>\n<AUTOSAR ', 1
    )
    any_repo.write("Config/Developer/Entity.arxml", text)
    model = any_repo.load()
    assert codes_of(model) == [codes.PARSE_DOCTYPE]
    assert sorted(model.components) == [f"{CT}/Swc"]


def test_one_unreadable_file_does_not_stop_the_load(any_repo: Repo) -> None:
    samples.write_sample_repo(any_repo.root, ecuc=False)
    any_repo.write(BROKEN, b"\x00\x01\x02 not xml at all")
    any_repo.write("Config/Developer/Empty.arxml", b"")
    model = any_repo.load()
    assert_sample_intact(model)
    assert sorted(codes_of(model)) == [codes.PARSE_MALFORMED, codes.PARSE_EMPTY]
    stats = any_repo.reader.last_stats
    assert stats is not None and (stats.files_total, stats.files_failed) == (9, 2)


# ---- files that move while they are read


def test_file_deleted_during_the_load(repo: Repo, monkeypatch: pytest.MonkeyPatch) -> None:
    samples.write_sample_repo(repo.root, ecuc=False)
    victim = repo.path(samples.F_SWC_M)

    def discover_then_delete(config: Any, **kwargs: Any) -> Discovery:
        found = real_discover(config, **kwargs)
        victim.unlink()  # the scan saw the file, the parser will not
        return found

    monkeypatch.setattr(reader_module, "discover", discover_then_delete)
    model = repo.load()
    monkeypatch.undo()
    vanished = findings_with(model, codes.FILE_VANISHED)
    assert [f.file for f in vanished] == [samples.F_SWC_M]
    assert vanished[0].severity is Severity.ERROR
    assert f"{CT}/SwcM" not in model.components and f"{CT}/SwcA" in model.components
    # a vanished file is not part of the fingerprint: the model matches the disk
    assert samples.F_SWC_M not in model.files
    assert repo.reader.is_stale(repo.config(), model) is False


def test_file_changed_during_the_load_is_read_again(
    repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = repo.write("Config/Developer/Moving.arxml", doc(package("P", swc("Before"))))
    real_read = parse._read_bytes
    calls: list[str] = []

    def read_then_rewrite(path: str) -> bytes:
        data = real_read(path)
        calls.append(path)
        if len(calls) == 1:  # a writer replaces the file right after the first read
            target.write_bytes(doc(package("P", swc("AfterTheChange"))).encode())
        return data

    monkeypatch.setattr(parse, "_read_bytes", read_then_rewrite)
    monkeypatch.setattr(parse, "_sleep", lambda _seconds: None)
    model = repo.load()
    monkeypatch.undo()
    assert len(calls) == 2
    assert model.findings == ()
    assert sorted(model.components) == ["/P/AfterTheChange"]
    source = model.files["Config/Developer/Moving.arxml"]
    assert (source.size, source.mtime_ns) == (target.stat().st_size, target.stat().st_mtime_ns)
    assert repo.reader.is_stale(repo.config(), model) is False


def test_file_that_keeps_changing_is_reported_and_not_cached(
    repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = repo.write("Config/Developer/Moving.arxml", doc(package("P", swc("Swc0"))))
    real_read = parse._read_bytes
    calls: list[str] = []

    def read_then_rewrite(path: str) -> bytes:
        data = real_read(path)
        calls.append(path)
        target.write_bytes(doc(package("P", swc(f"Swc{len(calls)}"))).encode() + b"\n" * len(calls))
        return data

    monkeypatch.setattr(parse, "_read_bytes", read_then_rewrite)
    monkeypatch.setattr(parse, "_sleep", lambda _seconds: None)
    model = repo.load()
    monkeypatch.undo()
    assert len(calls) == 3  # three attempts, then give up
    assert codes_of(model) == [codes.FILE_UNSTABLE]
    assert model.findings[0].severity is Severity.WARNING
    # the next load parses the file again: an unstable read is never cached
    again = repo.load()
    stats = repo.reader.last_stats
    assert stats is not None and stats.files_parsed == 1
    assert again.findings == () and sorted(again.components) == ["/P/Swc3"]


# ---- permissions


def test_permission_denied(repo: Repo, monkeypatch: pytest.MonkeyPatch) -> None:
    samples.write_sample_repo(repo.root, ecuc=False)
    denied = str(repo.path(samples.F_SWC_M))
    real_open = parse._open_shared

    def open_or_deny(path: str) -> Any:
        if os.path.normcase(path) == os.path.normcase(denied):
            raise PermissionError(13, "Permission denied", path)
        return real_open(path)

    monkeypatch.setattr(parse, "_open_shared", open_or_deny)
    model = repo.load()
    monkeypatch.undo()
    unreadable = findings_with(model, codes.FILE_UNREADABLE)
    assert [f.file for f in unreadable] == [samples.F_SWC_M]
    assert "Permission denied" in unreadable[0].message
    assert f"{CT}/SwcM" not in model.components and f"{CT}/SwcA" in model.components
    # the file exists, so it is fingerprinted with what the scan saw (no hash)
    source = model.files[samples.F_SWC_M]
    assert source.sha256 is None and source.size == repo.path(samples.F_SWC_M).stat().st_size
    assert repo.reader.is_stale(repo.config(), model) is False
    # a failed read is not cached: once the file is readable it is loaded
    assert f"{CT}/SwcM" in repo.load().components


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root can read any file"
)
def test_permission_denied_by_the_file_system(repo: Repo) -> None:
    samples.write_sample_repo(repo.root, ecuc=False)
    repo.path(samples.F_SWC_M).chmod(0)
    try:
        model = repo.load()
    finally:
        repo.path(samples.F_SWC_M).chmod(0o644)
    assert [f.file for f in findings_with(model, codes.FILE_UNREADABLE)] == [samples.F_SWC_M]


def test_sharing_violation_is_retried(repo: Repo, monkeypatch: pytest.MonkeyPatch) -> None:
    """Logic only: a real Windows sharing violation is listed as unverified in HANDOFF.md."""
    repo.write("Config/Developer/Locked.arxml", doc(package("P", swc("Locked"))))

    class SharingViolation(PermissionError):
        winerror = 32

    attempts: list[str] = []
    pauses: list[float] = []

    def open_busy_twice(path: str, mode: str) -> Any:
        attempts.append(path)
        if len(attempts) <= 2:
            raise SharingViolation(13, "The process cannot access the file", path)
        return open(path, mode)  # noqa: SIM115 - handed to the caller

    monkeypatch.setattr(parse, "open", open_busy_twice, raising=False)
    monkeypatch.setattr(parse, "_sleep", pauses.append)
    model = repo.load()
    monkeypatch.undo()
    assert model.findings == () and "/P/Locked" in model.components
    assert len(attempts) == 3 and pauses == [0.05, 0.1]


def test_sharing_violation_that_does_not_end_is_a_finding(
    repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo.write("Config/Developer/Locked.arxml", doc(package("P", swc("Locked"))))

    class SharingViolation(PermissionError):
        winerror = 32

    def always_busy(path: str, mode: str) -> Any:
        raise SharingViolation(13, "The process cannot access the file", path)

    monkeypatch.setattr(parse, "open", always_busy, raising=False)
    monkeypatch.setattr(parse, "_sleep", lambda _seconds: None)
    model = repo.load()
    monkeypatch.undo()
    assert codes_of(model) == [codes.FILE_UNREADABLE]


def test_unexpected_error_in_one_file_is_a_finding(
    repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    samples.write_sample_repo(repo.root, ecuc=False)
    real = parse._extract_dom

    def explode(data: bytes, rel_path: str, *args: Any) -> Any:
        if rel_path == samples.F_SWC_M:
            raise RuntimeError("bug in the extractor")
        return real(data, rel_path, *args)

    monkeypatch.setattr(parse, "_extract_dom", explode)
    model = repo.load()
    monkeypatch.undo()
    assert [f.file for f in findings_with(model, codes.INTERNAL)] == [samples.F_SWC_M]
    assert f"{CT}/SwcA" in model.components


# ---- cancel


def many_small_files(repo: Repo, count: int) -> None:
    for index in range(count):
        repo.write(
            f"Config/Developer/Many/Swc{index:03d}.arxml",
            doc(package("Many", swc(f"Swc{index:03d}"))),
        )


def test_cancel_in_the_middle_of_a_load(repo: Repo, monkeypatch: pytest.MonkeyPatch) -> None:
    many_small_files(repo, 200)
    reads: list[str] = []
    real_read = parse._read_bytes

    def slow_read(path: str) -> bytes:
        reads.append(path)
        time.sleep(0.005)
        return real_read(path)

    monkeypatch.setattr(parse, "_read_bytes", slow_read)
    ctx = repo.context(workers=2, cancel_after_progress=20)
    started = time.perf_counter()
    with pytest.raises(JobCancelled):
        repo.load(ctx=ctx)
    elapsed = time.perf_counter() - started
    monkeypatch.undo()
    assert ctx.cancelled
    # stopped promptly: far from all files were read, and nothing waited for the rest
    assert 0 < len(reads) < 60
    assert elapsed < 2.0
    # the reader is usable afterwards and the next load is complete
    model = repo.load()
    assert len(model.components) == 200 and model.findings == ()


def test_cancel_before_the_load_starts(repo: Repo) -> None:
    many_small_files(repo, 3)
    ctx = repo.context()
    ctx.cancel()
    with pytest.raises(JobCancelled):
        repo.load(ctx=ctx)
    assert ctx.blocking_calls == 0


def test_cancel_during_a_large_streamed_file(repo: Repo, monkeypatch: pytest.MonkeyPatch) -> None:
    body = "".join(swc(f"Swc{index}") for index in range(4000))
    repo.write("Config/Developer/Big.arxml", doc(package("Big", body)))
    monkeypatch.setattr(parse, "_STREAM_CHUNK", 4096)
    ctx = repo.context()
    checks = 0
    real_check = ctx.check_cancelled

    def cancel_after_some_chunks() -> None:
        nonlocal checks
        checks += 1
        if checks == 12:
            ctx.cancel()
        real_check()

    monkeypatch.setattr(ctx, "check_cancelled", cancel_after_some_chunks)
    with pytest.raises(JobCancelled):
        repo.load(ctx=ctx, large_file_bytes=0)
    assert checks < 40  # the file has more than 50 chunks: the parse stopped early


def test_load_runs_in_the_worker_pool_and_reports_progress(repo: Repo) -> None:
    samples.write_sample_repo(repo.root, ecuc=False)
    ctx = repo.context(workers=3)
    repo.load(ctx=ctx)
    # scan + cache read + one task per file + cache write + model build
    assert ctx.blocking_calls == 1 + 1 + 7 + 1 + 1
    assert ctx.worker_threads and all(name.startswith("test-worker") for name in ctx.worker_threads)
    assert ctx.events[0] == (0, 0, "Scanning folders")
    assert ctx.events[-1] == (8, 8, "Model ready")
    done = [event[0] for event in ctx.events[1:]]
    assert done == sorted(done) and {event[1] for event in ctx.events[1:]} == {8}


def test_load_never_parses_on_the_event_loop(repo: Repo, monkeypatch: pytest.MonkeyPatch) -> None:
    samples.write_sample_repo(repo.root, ecuc=False)
    loop_threads: list[int] = []
    parse_threads: set[int] = set()
    real = parse._extract_dom

    def spy(*args: Any) -> Any:
        import threading

        parse_threads.add(threading.get_ident())
        return real(*args)

    monkeypatch.setattr(parse, "_extract_dom", spy)

    async def run() -> None:
        import threading

        loop_threads.append(threading.get_ident())
        await repo.reader.load(repo.config(), repo.context())

    asyncio.run(run())
    assert parse_threads and loop_threads[0] not in parse_threads


def test_byte_budget_limits_parallel_dom_bytes() -> None:
    budget = parse.ByteBudget(100)
    budget.acquire(60, lambda: None)
    budget.acquire(40, lambda: None)
    assert budget.used == 100
    waited: list[int] = []

    def cancelled() -> None:
        waited.append(1)
        raise JobCancelled()

    with pytest.raises(JobCancelled):
        budget.acquire(1, cancelled)  # full: waits, and the wait can be cancelled
    assert waited and budget.used == 100
    budget.release(60)
    budget.release(40)
    budget.acquire(500, lambda: None)  # larger than the whole budget: runs alone
    assert budget.used == 500


# ---- very large file


@pytest.mark.slow
def test_very_large_file_uses_the_streaming_path(
    repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """About 60 MB in one file: read by the streaming path, identical to the DOM result."""
    filler = "x" * 2000
    count = 24_000
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<AUTOSAR xmlns="{samples.NS}">\n<AR-PACKAGES>\n<AR-PACKAGE>\n'
        "<SHORT-NAME>Big</SHORT-NAME>\n<ELEMENTS>\n"
    ]
    for index in range(count):
        parts.append(
            f"<APPLICATION-SW-COMPONENT-TYPE>\n<SHORT-NAME>Swc{index}</SHORT-NAME>\n"
            f'<DESC><L-2 L="EN">{filler}</L-2></DESC>\n<PORTS>\n'
            f"<P-PORT-PROTOTYPE>\n<SHORT-NAME>Pp</SHORT-NAME>\n"
            f'<PROVIDED-INTERFACE-TREF DEST="SENDER-RECEIVER-INTERFACE">{IF}/If{index % 7}'
            "</PROVIDED-INTERFACE-TREF>\n</P-PORT-PROTOTYPE>\n</PORTS>\n"
            "</APPLICATION-SW-COMPONENT-TYPE>\n"
            # an element kind the reader does not know: must be skipped and freed
            f"<SYSTEM-SIGNAL>\n<SHORT-NAME>Sig{index}</SHORT-NAME>\n"
            f'<DESC><L-2 L="EN">{filler[:400]}</L-2></DESC>\n</SYSTEM-SIGNAL>\n'
        )
    parts.append("</ELEMENTS>\n</AR-PACKAGE>\n</AR-PACKAGES>\n</AUTOSAR>\n")
    big = repo.path("Config/Developer/Big.arxml")
    big.parent.mkdir(parents=True, exist_ok=True)
    big.write_bytes("".join(parts).encode())
    del parts
    size = big.stat().st_size
    assert 55_000_000 < size < 70_000_000

    streamed: list[str] = []
    real_stream = parse._extract_stream

    def spy(path: str, *args: Any) -> Any:
        streamed.append(path)
        return real_stream(path, *args)

    monkeypatch.setattr(parse, "_extract_stream", spy)
    model = repo.load()  # default threshold: 50 MB
    monkeypatch.undo()
    assert streamed == [str(big)]
    assert len(model.components) == count and len(model.ports) == count
    # 14 lines per index, the first component starts on line 7
    assert model.components["/Big/Swc100"].origin.line == 7 + 100 * 14
    # Beyond line 65535 libxml2 no longer stores the element's own line: it reports the
    # line of the first text inside the element, here the SHORT-NAME on the next line.
    last = model.components[f"/Big/Swc{count - 1}"]
    assert last.origin.line == 7 + (count - 1) * 14 + 1
    assert model.ports[f"/Big/Swc{count - 1}/Pp"].interface_path == f"{IF}/If{(count - 1) % 7}"
    assert model.files["Config/Developer/Big.arxml"].sha256 is not None
    assert {f.code for f in model.findings} == {codes.REF_UNRESOLVED}

    # the DOM path gives exactly the same records
    suffixes = ("/Os/OsTask",)
    by_dom = parse.parse_file(str(big), "Big.arxml", 10**12, suffixes, lambda: None)
    by_stream = parse.parse_file(str(big), "Big.arxml", 0, suffixes, lambda: None)
    assert by_dom == by_stream
