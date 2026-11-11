"""Per-file records: what one ARXML file contributes to the model.

Records are plain tuples of str, int, float, bool, None and nested tuples. Plain tuples
unpickle about three times faster than named tuples (measured), and a cache blob made
only of builtins can be read with an unpickler that refuses every global. The field
order of each record is documented below and checked by mypy through the aliases.

A reference is ``(path, dest)``. ``path`` is absolute (starts with ``/``) or, for a
relative reference, the triple ``base label, package path, text`` joined by
``RELATIVE_SEP``; the builder resolves it through the reference bases of all files.
"""

from __future__ import annotations

from typing import NamedTuple, TypeAlias

#: Bump when the meaning or layout of any record changes: old cache entries are dropped.
CACHE_FORMAT = 1

RELATIVE_SEP = "\x1f"

Ref: TypeAlias = tuple[str, str | None]  # (path, DEST attribute)

#: (code, severity value, message, object path, line)
RawFinding: TypeAlias = tuple[str, str, str, str | None, int | None]

#: (package path, short label, is default, is global, target package path, line)
RefBaseRec: TypeAlias = tuple[str, str, bool, bool, str | None, int | None]

#: (path, name, element tag, line, uuid)
ComponentRec: TypeAlias = tuple[str, str, str, int | None, str | None]

#: (com spec tag, target reference, init value text, queue length)
ComSpecRec: TypeAlias = tuple[str, Ref | None, str | None, int | None]

#: (path, name, swc path, port tag, interface reference, com specs, line, uuid)
PortRec: TypeAlias = tuple[
    str, str, str, str, Ref | None, tuple[ComSpecRec, ...], int | None, str | None
]

#: (path, name, swc path, line, uuid)
BehaviorRec: TypeAlias = tuple[str, str, str, int | None, str | None]

#: (path, name, behavior path, swc path, symbol, concurrent, min start interval, line, uuid)
RunnableRec: TypeAlias = tuple[
    str, str, str, str, str | None, bool, float | None, int | None, str | None
]

#: (path, name, runnable path, swc path, access tag, port ref, target ref, line, uuid)
AccessRec: TypeAlias = tuple[
    str, str, str, str, str, Ref | None, Ref | None, int | None, str | None
]

#: (path, name, behavior path, swc path, event tag, runnable ref, port ref, target ref,
#:  second target ref, period, event source ref, line, uuid)
EventRec: TypeAlias = tuple[
    str,
    str,
    str,
    str,
    str,
    Ref | None,
    Ref | None,
    Ref | None,
    Ref | None,
    float | None,
    Ref | None,
    int | None,
    str | None,
]

#: (path, name, composition path, type reference, line, uuid)
PrototypeRec: TypeAlias = tuple[str, str, str, Ref | None, int | None, str | None]

#: (path, name, composition path, connector tag, provider component ref, provider port ref,
#:  requester component ref, requester port ref, line, uuid)
#: A component ref of None means the composition's own (outer) port.
ConnectorRec: TypeAlias = tuple[
    str, str, str, str, Ref | None, Ref | None, Ref | None, Ref | None, int | None, str | None
]

#: (name, element tag, type reference, is queued, init value text)
ElementRec: TypeAlias = tuple[str, str, Ref | None, bool, str | None]

#: (name, direction, type reference)
ArgumentRec: TypeAlias = tuple[str, str | None, Ref | None]

#: (name, arguments, possible error references)
OperationRec: TypeAlias = tuple[str, tuple[ArgumentRec, ...], tuple[Ref, ...]]

#: (path, name, interface tag, is service, elements, operations, error names, line, uuid)
InterfaceRec: TypeAlias = tuple[
    str,
    str,
    str,
    bool,
    tuple[ElementRec, ...],
    tuple[OperationRec, ...],
    tuple[str, ...],
    int | None,
    str | None,
]

#: (path, name, data type tag, category, base type ref, compu method ref, constraint ref,
#:  unit ref, referenced type ref, element names, element type refs, array size, line, uuid)
DataTypeRec: TypeAlias = tuple[
    str,
    str,
    str,
    str | None,
    Ref | None,
    Ref | None,
    Ref | None,
    Ref | None,
    Ref | None,
    tuple[str, ...],
    tuple[Ref | None, ...],
    int | None,
    int | None,
    str | None,
]

#: (path, name, size in bits, encoding, line, uuid)
BaseTypeRec: TypeAlias = tuple[str, str, int | None, str | None, int | None, str | None]

#: (path, name, category, factor, offset, enumeration values, unit ref, line, uuid)
CompuMethodRec: TypeAlias = tuple[
    str,
    str,
    str | None,
    float | None,
    float | None,
    tuple[tuple[int, str], ...],
    Ref | None,
    int | None,
    str | None,
]

#: (path, name, lower limit, upper limit, line, uuid)
ConstraintRec: TypeAlias = tuple[str, str, float | None, float | None, int | None, str | None]

#: (path, name, display name, line, uuid)
UnitRec: TypeAlias = tuple[str, str, str | None, int | None, str | None]

#: (path, name, (application type ref, implementation type ref) pairs, line, uuid)
MappingSetRec: TypeAlias = tuple[
    str, str, tuple[tuple[Ref | None, Ref | None], ...], int | None, str | None
]

#: (path, name, value text, line, uuid)
ConstantRec: TypeAlias = tuple[str, str, str | None, int | None, str | None]

#: (path, name, mode names, line, uuid)
ModeGroupRec: TypeAlias = tuple[str, str, tuple[str, ...], int | None, str | None]

#: (path, name, definition ref, parameters (definition, value),
#:  references (definition, value, dest), line, uuid)
EcucContainerRec: TypeAlias = tuple[
    str,
    str,
    str,
    tuple[tuple[str, str | None], ...],
    tuple[tuple[str, str | None, str | None], ...],
    int | None,
    str | None,
]

# ---- file status

STATUS_OK = "ok"
STATUS_MALFORMED = "malformed"
STATUS_EMPTY = "empty"
STATUS_UNSUPPORTED = "unsupported-schema"
STATUS_UNREADABLE = "unreadable"
STATUS_VANISHED = "vanished"
STATUS_INTERNAL = "internal-error"

#: Results that depend only on the file content may be cached, failures included.
CACHEABLE_STATUS = frozenset({STATUS_OK, STATUS_MALFORMED, STATUS_EMPTY, STATUS_UNSUPPORTED})


class FileRecords(NamedTuple):
    """Everything extracted from one file, plus the fingerprint of what was read."""

    format: int
    rel_path: str
    size: int
    mtime_ns: int
    sha256: str | None
    status: str
    stable: bool  # False: the file kept changing while it was read (never cached)
    schema: str | None  # XSD file name from xsi:schemaLocation, e.g. "AUTOSAR_4-2-2.xsd"
    findings: tuple[RawFinding, ...]
    ref_bases: tuple[RefBaseRec, ...]
    components: tuple[ComponentRec, ...]
    ports: tuple[PortRec, ...]
    behaviors: tuple[BehaviorRec, ...]
    runnables: tuple[RunnableRec, ...]
    accesses: tuple[AccessRec, ...]
    events: tuple[EventRec, ...]
    prototypes: tuple[PrototypeRec, ...]
    connectors: tuple[ConnectorRec, ...]
    interfaces: tuple[InterfaceRec, ...]
    data_types: tuple[DataTypeRec, ...]
    base_types: tuple[BaseTypeRec, ...]
    compu_methods: tuple[CompuMethodRec, ...]
    constraints: tuple[ConstraintRec, ...]
    units: tuple[UnitRec, ...]
    mapping_sets: tuple[MappingSetRec, ...]
    constants: tuple[ConstantRec, ...]
    mode_groups: tuple[ModeGroupRec, ...]
    ecuc_containers: tuple[EcucContainerRec, ...]

    @property
    def cacheable(self) -> bool:
        return self.stable and self.status in CACHEABLE_STATUS


def empty_records(
    rel_path: str,
    size: int,
    mtime_ns: int,
    status: str,
    findings: tuple[RawFinding, ...] = (),
    *,
    sha256: str | None = None,
    stable: bool = True,
    schema: str | None = None,
) -> FileRecords:
    """Records of a file that contributed no objects (failed, empty or unsupported)."""
    return FileRecords(
        format=CACHE_FORMAT,
        rel_path=rel_path,
        size=size,
        mtime_ns=mtime_ns,
        sha256=sha256,
        status=status,
        stable=stable,
        schema=schema,
        findings=findings,
        ref_bases=(),
        components=(),
        ports=(),
        behaviors=(),
        runnables=(),
        accesses=(),
        events=(),
        prototypes=(),
        connectors=(),
        interfaces=(),
        data_types=(),
        base_types=(),
        compu_methods=(),
        constraints=(),
        units=(),
        mapping_sets=(),
        constants=(),
        mode_groups=(),
        ecuc_containers=(),
    )


#: Number of fields of ``FileRecords``; a cache blob with another length is rejected.
FIELD_COUNT = len(FileRecords._fields)
