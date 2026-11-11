"""Read one ARXML file into ``FileRecords``. Runs in a worker thread.

Two strategies produce identical records:

* DOM: read the whole file, parse it (lxml releases the GIL), walk the packages.
* Streaming, for files above ``ProjectConfig.large_file_bytes``: feed a pull parser
  chunk by chunk and drop every finished element, so memory stays flat.

Expected problems (missing file, permission denied, malformed XML, wrong schema) become
findings inside the returned records. ``parse_file`` raises only ``JobCancelled``.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from collections.abc import Callable
from typing import Any, Final

from lxml import etree

from porttool.core.errors import JobCancelled
from porttool.core.results import Severity
from porttool.reader import codes, extract
from porttool.reader.extract import (
    AR_PACKAGE,
    AR_PACKAGES,
    AUTOSAR,
    ECUC_CONTAINER,
    ELEMENTS,
    REFERENCE_BASE,
    REFERENCE_BASES,
    SHORT_NAME,
    Collector,
)
from porttool.reader.records import (
    CACHE_FORMAT,
    STATUS_EMPTY,
    STATUS_INTERNAL,
    STATUS_MALFORMED,
    STATUS_OK,
    STATUS_UNREADABLE,
    STATUS_UNSUPPORTED,
    STATUS_VANISHED,
    FileRecords,
    RawFinding,
    empty_records,
)

_LOG = logging.getLogger(__name__)

_ERROR: Final = Severity.ERROR.value
_WARNING: Final = Severity.WARNING.value

_XSI_SCHEMA_LOCATION: Final = "{http://www.w3.org/2001/XMLSchema-instance}schemaLocation"
_VARIATION_TOKEN: Final = b"VARIATION-POINT"
_STREAM_CHUNK: Final = 1024 * 1024
_STREAM_TAGS: Final = (SHORT_NAME, ELEMENTS, ECUC_CONTAINER, REFERENCE_BASE, AR_PACKAGE)

#: Attempts to get a stable read of a file that changes while it is read.
_READ_ATTEMPTS: Final = 3
#: Attempts to open a file that another process holds open without sharing.
_SHARING_ATTEMPTS: Final = 3
_SHARING_VIOLATION: Final = 32  # Windows ERROR_SHARING_VIOLATION

#: Never resolve entities, never touch the network, never load a DTD. Comments and
#: processing instructions are dropped by the parser so that no walker has to skip them.
_PARSER_OPTIONS: Final[dict[str, Any]] = {
    "resolve_entities": False,
    "no_network": True,
    "load_dtd": False,
    "dtd_validation": False,
    "attribute_defaults": False,
    "remove_comments": True,
    "remove_pis": True,
    "remove_blank_text": True,
    "collect_ids": False,
    "recover": False,
}

_thread_state = threading.local()

CheckCancelled = Callable[[], None]

# Seams for tests (monkeypatched to simulate Windows behaviour and racing writers).
_sleep: Callable[[float], None] = time.sleep


def _parser() -> Any:
    """The calling thread's XML parser. lxml parsers must not be shared between threads."""
    parser = getattr(_thread_state, "parser", None)
    if parser is None:
        parser = etree.XMLParser(**_PARSER_OPTIONS)
        _thread_state.parser = parser
    return parser


class ByteBudget:
    """Limits how many source bytes are held as DOM trees at the same time.

    A DOM tree needs about five times the file size (measured). Without a limit, a pool
    of sixteen workers that all pick 40 MB files would need several gigabytes.
    A file larger than the whole budget still runs, alone.
    """

    def __init__(self, limit: int) -> None:
        self._limit = max(1, limit)
        self._used = 0
        self._condition = threading.Condition()

    def acquire(self, amount: int, check_cancelled: CheckCancelled) -> None:
        with self._condition:
            while self._used > 0 and self._used + amount > self._limit:
                self._condition.wait(0.1)
                check_cancelled()
            self._used += amount

    def release(self, amount: int) -> None:
        with self._condition:
            self._used -= amount
            self._condition.notify_all()

    @property
    def used(self) -> int:
        with self._condition:
            return self._used


# --------------------------------------------------------------------------- file access


def _stat(path: str) -> tuple[int, int]:
    info = os.stat(path)
    return info.st_size, info.st_mtime_ns


def _open_shared(path: str) -> Any:
    """Open for reading; retry a Windows sharing violation with a short backoff."""
    attempt = 1
    while True:
        try:
            return open(path, "rb")  # noqa: SIM115 - the caller closes it
        except PermissionError as error:
            sharing = getattr(error, "winerror", None) == _SHARING_VIOLATION
            if not sharing or attempt >= _SHARING_ATTEMPTS:
                raise
            _sleep(0.05 * attempt)
            attempt += 1


def _read_bytes(path: str) -> bytes:
    with _open_shared(path) as handle:
        data: bytes = handle.read()
    return data


# --------------------------------------------------------------------------- walkers


def _schema_of(root: Any) -> str | None:
    """XSD file name from xsi:schemaLocation, e.g. ``AUTOSAR_4-2-2.xsd``."""
    location = root.get(_XSI_SCHEMA_LOCATION)
    if not location:
        return None
    parts = str(location).split()
    return parts[-1].replace("\\", "/").rpartition("/")[2] if parts else None


def _unsupported_root(root: Any) -> RawFinding | None:
    tag = root.tag
    if tag == AUTOSAR:
        return None
    described = tag if isinstance(tag, str) else "?"
    return (
        codes.SCHEMA_UNSUPPORTED,
        _ERROR,
        f"Not an AUTOSAR 4.x file (root element {described}). The file was not loaded.",
        None,
        None,
    )


def _sniff_unsupported(head: bytes) -> RawFinding | None:
    """Finding if the root element that starts in ``head`` is not AUTOSAR 4.x.

    None when the root is AUTOSAR 4.x, and also when ``head`` does not reach the root
    element or is not well-formed: the full parse then decides.
    """
    sniffer = etree.XMLPullParser(events=("start",), **_PARSER_OPTIONS)
    try:
        sniffer.feed(head)
        for _event, el in sniffer.read_events():
            return _unsupported_root(el)
    except etree.LxmlError:
        return None
    return None


def _doctype_finding(root: Any) -> RawFinding | None:
    if not root.getroottree().docinfo.doctype:
        return None
    return (
        codes.PARSE_DOCTYPE,
        _WARNING,
        "The file declares a DOCTYPE. Entity references are not expanded and their text "
        "is missing from the model.",
        None,
        None,
    )


def _walk_packages(holder: Any, prefix: str, out: Collector) -> None:
    dispatch = extract.dispatch_unit
    for package in holder.iterchildren(AR_PACKAGE):
        name = extract.short_name(package)
        if name is None:
            out.missing_name(package, prefix or "/")
            continue
        path = f"{prefix}/{name}"
        for child in package:
            tag = child.tag
            if tag == ELEMENTS:
                for unit in child:
                    dispatch(unit, path, out)
            elif tag == AR_PACKAGES:
                _walk_packages(child, path, out)
            elif tag == REFERENCE_BASES:
                for base in child.iterchildren(REFERENCE_BASE):
                    extract.reference_base(base, path, out)


def _package_path(el: Any) -> str | None:
    """Path of the innermost AR-PACKAGE around ``el`` (streaming: read from ancestors)."""
    names: list[str] = []
    for package in el.iterancestors(AR_PACKAGE):
        name = extract.short_name(package)
        if name is None:
            return None
        names.append(name)
    names.reverse()
    return "/" + "/".join(names)


def _finish(
    rel_path: str,
    size: int,
    mtime_ns: int,
    sha256: str | None,
    stable: bool,
    schema: str | None,
    out: Collector,
) -> FileRecords:
    return FileRecords(
        format=CACHE_FORMAT,
        rel_path=rel_path,
        size=size,
        mtime_ns=mtime_ns,
        sha256=sha256,
        status=STATUS_OK,
        stable=stable,
        schema=schema,
        findings=tuple(out.findings),
        ref_bases=tuple(out.ref_bases),
        components=tuple(out.components),
        ports=tuple(out.ports),
        behaviors=tuple(out.behaviors),
        runnables=tuple(out.runnables),
        accesses=tuple(out.accesses),
        events=tuple(out.events),
        prototypes=tuple(out.prototypes),
        connectors=tuple(out.connectors),
        interfaces=tuple(out.interfaces),
        data_types=tuple(out.data_types),
        base_types=tuple(out.base_types),
        compu_methods=tuple(out.compu_methods),
        constraints=tuple(out.constraints),
        units=tuple(out.units),
        mapping_sets=tuple(out.mapping_sets),
        constants=tuple(out.constants),
        mode_groups=tuple(out.mode_groups),
        ecuc_containers=tuple(out.ecuc_containers),
    )


def _malformed(error: Exception) -> RawFinding:
    line = getattr(error, "lineno", None)
    return (
        codes.PARSE_MALFORMED,
        _ERROR,
        f"The file is not well-formed XML and was not loaded: {error}",
        None,
        line if isinstance(line, int) and line > 0 else None,
    )


def _extract_dom(
    data: bytes, rel_path: str, size: int, mtime_ns: int, stable: bool, suffixes: tuple[str, ...]
) -> FileRecords:
    sha256 = hashlib.sha256(data).hexdigest()
    if not data:
        finding: RawFinding = (
            codes.PARSE_EMPTY,
            _ERROR,
            "The file is empty and was not loaded.",
            None,
            None,
        )
        return empty_records(
            rel_path, size, mtime_ns, STATUS_EMPTY, (finding,), sha256=sha256, stable=stable
        )
    try:
        root = etree.fromstring(data, _parser())
    except etree.LxmlError as error:
        return empty_records(
            rel_path,
            size,
            mtime_ns,
            STATUS_MALFORMED,
            (_malformed(error),),
            sha256=sha256,
            stable=stable,
        )
    unsupported = _unsupported_root(root)
    if unsupported is not None:
        return empty_records(
            rel_path,
            size,
            mtime_ns,
            STATUS_UNSUPPORTED,
            (unsupported,),
            sha256=sha256,
            stable=stable,
        )
    # Cheap skip of the variation-point scan. The token cannot be found by a byte search
    # in UTF-16 or UTF-32 (a NUL among the first bytes), so those files are always scanned.
    out = Collector(suffixes, check_variation=_VARIATION_TOKEN in data or b"\x00" in data[:4])
    doctype = _doctype_finding(root)
    if doctype is not None:
        out.findings.append(doctype)
    for holder in root.iterchildren(AR_PACKAGES):
        _walk_packages(holder, "", out)
    return _finish(rel_path, size, mtime_ns, sha256, stable, _schema_of(root), out)


def _sweep(holder: Any, stop: Any, out: Collector) -> None:
    """Extract and drop the finished children of a package's ELEMENTS, up to ``stop``."""
    package = _package_path(holder)
    # Never iterate and remove at the same time: lxml would stop after the first removal.
    while len(holder):
        unit = holder[0]
        if unit is stop:
            break
        if package is not None:
            extract.dispatch_unit(unit, package, out, ecuc_done=True)
        holder.remove(unit)


def _handle_stream_event(el: Any, out: Collector) -> None:
    tag = el.tag
    if tag == SHORT_NAME:
        owner = el.getparent()
        if owner is None:
            return
        holder = owner.getparent()
        if holder is None or holder.tag != ELEMENTS:
            return
        package = holder.getparent()
        if package is None or package.tag != AR_PACKAGE:
            return
        # A new element of the package starts: everything before it is complete.
        if owner.getprevious() is not None:
            _sweep(holder, owner, out)
    elif tag == ECUC_CONTAINER:
        extract.ecuc_container(el, out)
        el.clear()
        parent = el.getparent()
        if parent is not None:
            previous = el.getprevious()
            while previous is not None:
                parent.remove(previous)
                previous = el.getprevious()
    elif tag == ELEMENTS:
        package = el.getparent()
        if package is not None and package.tag == AR_PACKAGE:
            _sweep(el, None, out)
    elif tag == REFERENCE_BASE:
        path = _package_path(el)
        if path is not None:
            extract.reference_base(el, path, out)
    elif tag == AR_PACKAGE:
        if extract.short_name(el) is None:
            # Same finding as the DOM walker; a package inside a nameless package is
            # not reported again (the DOM walker never descends into one).
            parent = _package_path(el)
            if parent is not None:
                out.missing_name(el, parent)


def _extract_stream(
    path: str,
    rel_path: str,
    size: int,
    mtime_ns: int,
    suffixes: tuple[str, ...],
    check_cancelled: CheckCancelled,
) -> FileRecords:
    """Parse a large file chunk by chunk. Memory stays flat: finished elements are dropped."""
    digest = hashlib.sha256()
    out = Collector(suffixes, check_variation=True)
    parser = etree.XMLPullParser(events=("end",), tag=_STREAM_TAGS, **_PARSER_OPTIONS)
    read_events = parser.read_events
    read = 0
    try:
        with _open_shared(path) as handle:
            while True:
                chunk = handle.read(_STREAM_CHUNK)
                if not chunk:
                    break
                if read == 0:
                    # A large file of another format would never match the tag filter
                    # and would be built as one huge tree: reject it on the first chunk.
                    early = _sniff_unsupported(chunk)
                    if early is not None:
                        return empty_records(rel_path, size, mtime_ns, STATUS_UNSUPPORTED, (early,))
                read += len(chunk)
                digest.update(chunk)
                parser.feed(chunk)
                for _event, el in read_events():
                    _handle_stream_event(el, out)
                check_cancelled()
        root = parser.close()
    except etree.LxmlError as error:
        # The parser stopped before the end of the file, so the read count proves
        # nothing; the caller compares the file's fingerprint before and after.
        return empty_records(rel_path, size, mtime_ns, STATUS_MALFORMED, (_malformed(error),))
    stable = read == size
    sha256 = digest.hexdigest()
    unsupported = _unsupported_root(root)
    if unsupported is not None:
        return empty_records(
            rel_path,
            size,
            mtime_ns,
            STATUS_UNSUPPORTED,
            (unsupported,),
            sha256=sha256,
            stable=stable,
        )
    doctype = _doctype_finding(root)
    if doctype is not None:
        out.findings.insert(0, doctype)
    return _finish(rel_path, size, mtime_ns, sha256, stable, _schema_of(root), out)


# --------------------------------------------------------------------------- entry point


def _failure(rel_path: str, status: str, code: str, message: str) -> FileRecords:
    return empty_records(rel_path, 0, 0, status, ((code, _ERROR, message, None, None),))


def _unstable_finding() -> RawFinding:
    return (
        codes.FILE_UNSTABLE,
        _WARNING,
        "The file kept changing while it was read. Its content in the model may be "
        "incomplete: reload when the file is no longer being written.",
        None,
        None,
    )


def _read_stable(
    path: str,
    rel_path: str,
    large_file_bytes: int,
    suffixes: tuple[str, ...],
    check_cancelled: CheckCancelled,
    budget: ByteBudget | None,
) -> FileRecords:
    records: FileRecords | None = None
    for attempt in range(1, _READ_ATTEMPTS + 1):
        check_cancelled()
        size, mtime_ns = _stat(path)
        if size > large_file_bytes:
            records = _extract_stream(path, rel_path, size, mtime_ns, suffixes, check_cancelled)
            after = _stat(path)
            stable = records.stable and after == (size, mtime_ns)
        else:
            reserved = size if budget is not None else 0
            if budget is not None:
                budget.acquire(reserved, check_cancelled)
            try:
                data = _read_bytes(path)
                after = _stat(path)
                stable = after == (size, mtime_ns) and len(data) == size
                if not stable and attempt < _READ_ATTEMPTS:
                    records = None
                else:
                    records = _extract_dom(data, rel_path, size, mtime_ns, stable, suffixes)
                del data
            finally:
                if budget is not None:
                    budget.release(reserved)
        if stable and records is not None:
            return records
        if attempt < _READ_ATTEMPTS:
            _sleep(0.05 * attempt)
    assert records is not None
    return records._replace(stable=False, findings=(*records.findings, _unstable_finding()))


def parse_file(
    abs_path: str,
    rel_path: str,
    large_file_bytes: int,
    ecuc_suffixes: tuple[str, ...],
    check_cancelled: CheckCancelled,
    budget: ByteBudget | None = None,
) -> FileRecords:
    """Read and extract one file. Thread-worker boundary: only ``JobCancelled`` escapes."""
    try:
        return _read_stable(
            abs_path, rel_path, large_file_bytes, ecuc_suffixes, check_cancelled, budget
        )
    except JobCancelled:
        raise
    except FileNotFoundError:
        return _failure(
            rel_path,
            STATUS_VANISHED,
            codes.FILE_VANISHED,
            "The file disappeared while the repository was being loaded.",
        )
    except OSError as error:
        return _failure(
            rel_path,
            STATUS_UNREADABLE,
            codes.FILE_UNREADABLE,
            f"The file could not be read: {error.strerror or error}.",
        )
    except Exception:  # noqa: BLE001 - thread-worker boundary, see docs/SHARED_CONTEXT.md
        _LOG.exception("Unexpected error while reading %s", rel_path)
        return _failure(
            rel_path,
            STATUS_INTERNAL,
            codes.INTERNAL,
            "Unexpected error while reading the file. It was not loaded; see the log.",
        )
