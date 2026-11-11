"""Merge the per-file records into one immutable ``Model``.

Pure Python and therefore GIL-bound: kept to two linear passes over the records.

Pass 1 registers every short-name path and settles duplicates (first file in path order
wins) and split elements. Pass 2 creates the model objects and resolves every reference
by absolute path. Every problem becomes a ``Finding``; nothing here raises for bad input.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Final

from porttool.core.config import ProjectConfig
from porttool.core.models import (
    AccessKind,
    AccessPoint,
    Argument,
    ArgumentDirection,
    ArxmlOwner,
    BaseType,
    Component,
    ComponentKind,
    ComponentPrototype,
    Composition,
    CompuMethod,
    ComSpec,
    Connector,
    ConnectorEnd,
    ConnectorKind,
    Constant,
    DataConstraint,
    DataElement,
    DataType,
    DataTypeCategory,
    DataTypeLevel,
    DataTypeMappingSet,
    InterfaceKind,
    Model,
    Operation,
    Origin,
    Port,
    PortDirection,
    PortInterface,
    Runnable,
    SourceFile,
    TaskMapping,
    Trigger,
    TriggerKind,
    Unit,
)
from porttool.core.results import Finding, Severity
from porttool.reader import codes
from porttool.reader.discovery import DiscoveredFile, compile_globs
from porttool.reader.ecuc import EcucLayout
from porttool.reader.extract import parse_int
from porttool.reader.records import (
    RELATIVE_SEP,
    STATUS_INTERNAL,
    STATUS_OK,
    STATUS_UNREADABLE,
    STATUS_VANISHED,
    FileRecords,
    Ref,
)

_COMPONENT_SUFFIX: Final = "-SW-COMPONENT-TYPE"

_COMPONENT_KINDS: Final[dict[str, ComponentKind]] = {
    "APPLICATION-SW-COMPONENT-TYPE": ComponentKind.APPLICATION,
    "SENSOR-ACTUATOR-SW-COMPONENT-TYPE": ComponentKind.SENSOR_ACTUATOR,
    "COMPLEX-DEVICE-DRIVER-SW-COMPONENT-TYPE": ComponentKind.COMPLEX_DEVICE_DRIVER,
    "ECU-ABSTRACTION-SW-COMPONENT-TYPE": ComponentKind.ECU_ABSTRACTION,
    "SERVICE-SW-COMPONENT-TYPE": ComponentKind.SERVICE,
    "SERVICE-PROXY-SW-COMPONENT-TYPE": ComponentKind.SERVICE_PROXY,
    "NV-BLOCK-SW-COMPONENT-TYPE": ComponentKind.NV_BLOCK,
    "PARAMETER-SW-COMPONENT-TYPE": ComponentKind.PARAMETER,
    "COMPOSITION-SW-COMPONENT-TYPE": ComponentKind.COMPOSITION,
}
_PORT_DIRECTIONS: Final[dict[str, PortDirection]] = {
    "P-PORT-PROTOTYPE": PortDirection.P,
    "R-PORT-PROTOTYPE": PortDirection.R,
    "PR-PORT-PROTOTYPE": PortDirection.PR,
}
_INTERFACE_KINDS: Final[dict[str, InterfaceKind]] = {
    "SENDER-RECEIVER-INTERFACE": InterfaceKind.SENDER_RECEIVER,
    "CLIENT-SERVER-INTERFACE": InterfaceKind.CLIENT_SERVER,
    "MODE-SWITCH-INTERFACE": InterfaceKind.MODE_SWITCH,
    "PARAMETER-INTERFACE": InterfaceKind.PARAMETER,
    "NV-DATA-INTERFACE": InterfaceKind.NV_DATA,
    "TRIGGER-INTERFACE": InterfaceKind.TRIGGER,
}
_VARIABLE_ACCESS_LISTS: Final = frozenset(
    {
        "DATA-READ-ACCESSS",
        "DATA-WRITE-ACCESSS",
        "DATA-SEND-POINTS",
        "DATA-RECEIVE-POINT-BY-ARGUMENTS",
        "DATA-RECEIVE-POINT-BY-VALUES",
    }
)
_ACCESS_KINDS: Final[dict[str, AccessKind]] = {
    "DATA-READ-ACCESSS": AccessKind.IMPLICIT_READ,
    "DATA-WRITE-ACCESSS": AccessKind.IMPLICIT_WRITE,
    "DATA-SEND-POINTS": AccessKind.EXPLICIT_SEND,
    "DATA-RECEIVE-POINT-BY-ARGUMENTS": AccessKind.EXPLICIT_READ,
    "DATA-RECEIVE-POINT-BY-VALUES": AccessKind.EXPLICIT_READ,
    "SYNCHRONOUS-SERVER-CALL-POINT": AccessKind.SYNC_SERVER_CALL,
    "ASYNCHRONOUS-SERVER-CALL-POINT": AccessKind.ASYNC_SERVER_CALL,
    "MODE-ACCESS-POINT": AccessKind.MODE_ACCESS,
    "MODE-SWITCH-POINT": AccessKind.MODE_SWITCH,
    "PARAMETER-ACCESS": AccessKind.PARAMETER_ACCESS,
}
_TRIGGER_KINDS: Final[dict[str, TriggerKind]] = {
    "TIMING-EVENT": TriggerKind.PERIODIC,
    "DATA-RECEIVED-EVENT": TriggerKind.DATA_RECEIVED,
    "DATA-RECEIVE-ERROR-EVENT": TriggerKind.DATA_RECEIVE_ERROR,
    "OPERATION-INVOKED-EVENT": TriggerKind.OPERATION_INVOKED,
    "SWC-MODE-SWITCH-EVENT": TriggerKind.MODE_SWITCH,
    "INIT-EVENT": TriggerKind.INIT,
    "BACKGROUND-EVENT": TriggerKind.BACKGROUND,
    "DATA-SEND-COMPLETED-EVENT": TriggerKind.DATA_SEND_COMPLETED,
}
_CONNECTOR_KINDS: Final[dict[str, ConnectorKind]] = {
    "ASSEMBLY-SW-CONNECTOR": ConnectorKind.ASSEMBLY,
    "DELEGATION-SW-CONNECTOR": ConnectorKind.DELEGATION,
}
_ARGUMENT_DIRECTIONS: Final[dict[str, ArgumentDirection]] = {
    "IN": ArgumentDirection.IN,
    "OUT": ArgumentDirection.OUT,
    "INOUT": ArgumentDirection.INOUT,
}

_SPLITABLE: Final = frozenset({*_COMPONENT_KINDS, "SWC-INTERNAL-BEHAVIOR"})

_INTERFACE_TAGS: Final = frozenset(_INTERFACE_KINDS)
_PORT_TAGS: Final = frozenset(_PORT_DIRECTIONS)
_DATA_TYPE_TAGS: Final = frozenset(
    {
        "IMPLEMENTATION-DATA-TYPE",
        "APPLICATION-PRIMITIVE-DATA-TYPE",
        "APPLICATION-ARRAY-DATA-TYPE",
        "APPLICATION-RECORD-DATA-TYPE",
    }
)
_IMPLEMENTATION_TAGS: Final = frozenset({"IMPLEMENTATION-DATA-TYPE"})
_APPLICATION_TAGS: Final = _DATA_TYPE_TAGS - _IMPLEMENTATION_TAGS
_ELEMENT_TYPE_TAGS: Final = _DATA_TYPE_TAGS | {"SW-BASE-TYPE"}
_TARGET_TAGS: Final = frozenset(
    {
        "VARIABLE-DATA-PROTOTYPE",
        "PARAMETER-DATA-PROTOTYPE",
        "CLIENT-SERVER-OPERATION",
        "MODE-DECLARATION-GROUP-PROTOTYPE",
        "TRIGGER",
    }
)
_EVENT_TARGET_TAGS: Final = _TARGET_TAGS | {"MODE-DECLARATION"}
_RUNNABLE_TAGS: Final = frozenset({"RUNNABLE-ENTITY"})
_PROTOTYPE_TAGS: Final = frozenset({"SW-COMPONENT-PROTOTYPE"})
_BASE_TYPE_TAGS: Final = frozenset({"SW-BASE-TYPE"})
_COMPU_TAGS: Final = frozenset({"COMPU-METHOD"})
_CONSTRAINT_TAGS: Final = frozenset({"DATA-CONSTR"})
_UNIT_TAGS: Final = frozenset({"UNIT"})
_MODE_GROUP_TAGS: Final = frozenset({"MODE-DECLARATION-GROUP"})
_ERROR_TAGS: Final = frozenset({"APPLICATION-ERROR"})
_VARIABLE_ACCESS_TAGS: Final = frozenset({"VARIABLE-ACCESS"})

#: Abstract element names that a DEST attribute may use for a concrete element.
_ABSTRACT_DEST: Final[dict[str, frozenset[str]]] = {
    "SENDER-RECEIVER-INTERFACE": frozenset({"PORT-INTERFACE", "DATA-INTERFACE"}),
    "NV-DATA-INTERFACE": frozenset({"PORT-INTERFACE", "DATA-INTERFACE"}),
    "PARAMETER-INTERFACE": frozenset({"PORT-INTERFACE", "DATA-INTERFACE"}),
    "CLIENT-SERVER-INTERFACE": frozenset({"PORT-INTERFACE"}),
    "MODE-SWITCH-INTERFACE": frozenset({"PORT-INTERFACE"}),
    "TRIGGER-INTERFACE": frozenset({"PORT-INTERFACE"}),
    "P-PORT-PROTOTYPE": frozenset({"PORT-PROTOTYPE", "ABSTRACT-PROVIDED-PORT-PROTOTYPE"}),
    "R-PORT-PROTOTYPE": frozenset({"PORT-PROTOTYPE", "ABSTRACT-REQUIRED-PORT-PROTOTYPE"}),
    "PR-PORT-PROTOTYPE": frozenset(
        {"PORT-PROTOTYPE", "ABSTRACT-PROVIDED-PORT-PROTOTYPE", "ABSTRACT-REQUIRED-PORT-PROTOTYPE"}
    ),
    "IMPLEMENTATION-DATA-TYPE": frozenset(
        {"AUTOSAR-DATA-TYPE", "ABSTRACT-IMPLEMENTATION-DATA-TYPE"}
    ),
    "APPLICATION-PRIMITIVE-DATA-TYPE": frozenset({"AUTOSAR-DATA-TYPE", "APPLICATION-DATA-TYPE"}),
    "APPLICATION-ARRAY-DATA-TYPE": frozenset(
        {"AUTOSAR-DATA-TYPE", "APPLICATION-DATA-TYPE", "APPLICATION-COMPOSITE-DATA-TYPE"}
    ),
    "APPLICATION-RECORD-DATA-TYPE": frozenset(
        {"AUTOSAR-DATA-TYPE", "APPLICATION-DATA-TYPE", "APPLICATION-COMPOSITE-DATA-TYPE"}
    ),
    "VARIABLE-DATA-PROTOTYPE": frozenset({"DATA-PROTOTYPE", "AUTOSAR-DATA-PROTOTYPE"}),
    "PARAMETER-DATA-PROTOTYPE": frozenset({"DATA-PROTOTYPE", "AUTOSAR-DATA-PROTOTYPE"}),
    "VARIABLE-ACCESS": frozenset({"ABSTRACT-ACCESS-POINT"}),
}
_ABSTRACT_COMPONENT: Final = frozenset({"SW-COMPONENT-TYPE", "ATOMIC-SW-COMPONENT-TYPE"})
_ABSTRACT_EVENT: Final = frozenset({"RTE-EVENT", "ABSTRACT-EVENT"})

_NOT_READ: Final = frozenset({STATUS_UNREADABLE, STATUS_INTERNAL})

_NEW: Final = 1
_MERGED: Final = 2
_DROPPED: Final = 0

CheckCancelled = Callable[[], None]


def _dest_matches(dest: str, tag: str) -> bool:
    if dest in _ABSTRACT_DEST.get(tag, ()):
        return True
    if tag.endswith(_COMPONENT_SUFFIX):
        return dest in _ABSTRACT_COMPONENT
    return tag.endswith("-EVENT") and dest in _ABSTRACT_EVENT


def _last(path: str) -> str:
    return path.rpartition("/")[2]


def _shown(ref: str) -> str:
    """A reference as the user wrote it (relative references are stored encoded)."""
    return ref if ref.startswith("/") else ref.rpartition(RELATIVE_SEP)[2]


class _Builder:
    def __init__(
        self,
        config: ProjectConfig,
        files: Sequence[DiscoveredFile],
        records: Mapping[str, FileRecords],
        layout: EcucLayout,
        generated_globs: Sequence[str],
        check_cancelled: CheckCancelled,
        extra_files: Sequence[SourceFile] = (),
    ) -> None:
        self.config = config
        self.records = records
        self.extra_files = extra_files
        self.layout = layout
        self.check_cancelled = check_cancelled
        self.is_generated = compile_globs(generated_globs)
        self.discovered = files
        #: (file index, repo-relative path, records) in path order: the merge order.
        self.ordered: list[tuple[int, str, FileRecords]] = [
            (index, f.rel_path, records[f.rel_path])
            for index, f in enumerate(files)
            if f.rel_path in records
        ]
        self.rel_of: dict[int, str] = {index: rel for index, rel, _ in self.ordered}
        self.findings: list[Finding] = []
        self.registry: dict[str, str] = {}  # short-name path -> element tag
        self.first_file: dict[str, int] = {}  # short-name path -> index of the defining file
        self.skip: set[int] = set()  # id() of records that pass 2 must not turn into objects
        self.dropped: set[tuple[int, str]] = set()  # (file, path) dropped as duplicates
        self.duplicates: list[tuple[str, int, int, str, str, int | None]] = []
        self.splits: dict[str, list[int]] = {}
        self.bases: dict[str, dict[str, str]] = {}
        self.global_bases: dict[str, str] = {}
        self.component_tags: set[str] = set(_COMPONENT_KINDS)
        self.event_tags: set[str] = set()
        self.queued_elements: set[str] = set()
        self.queued_ports: set[tuple[str, str]] = set()

    # ---- findings

    def add(
        self,
        code: str,
        severity: Severity,
        message: str,
        *,
        path: str | None = None,
        file: str | None = None,
        line: int | None = None,
    ) -> None:
        if line is not None:
            message = f"{message} (line {line})"
        self.findings.append(
            Finding(code=code, severity=severity, message=message, object_path=path, file=file)
        )

    def file_findings(self) -> None:
        for _index, rel, records in self.ordered:
            for code, severity, message, path, line in records.findings:
                self.add(code, Severity(severity), message, path=path, file=rel, line=line)

    # ---- references

    def absolute(self, ref: str) -> str | None:
        """Absolute path of a reference as recorded by the extractor, or None."""
        if ref.startswith("/"):
            return ref
        base, package, text = ref.split(RELATIVE_SEP)
        current = package
        while current:
            known = self.bases.get(current)
            if known is not None:
                target = known.get(base)
                if target is not None:
                    return f"{target.rstrip('/')}/{text}"
            current = current.rpartition("/")[0]
        if base:
            target = self.global_bases.get(base)
            if target is not None:
                return f"{target.rstrip('/')}/{text}"
        return None

    def resolve(
        self,
        ref: Ref | None,
        expected: frozenset[str] | set[str],
        what: str,
        owner: str,
        file: str,
        line: int | None,
    ) -> str | None:
        """Absolute path of ``ref``. Reports what is wrong with it; never raises."""
        if ref is None:
            return None
        path, dest = ref
        tag = self.registry.get(path)
        if tag is not None and tag in expected and (dest == tag or dest is None):
            return path  # the common case
        if tag is None and not path.startswith("/"):
            resolved = self.absolute(path)
            if resolved is None:
                self.add(
                    codes.REF_RELATIVE_UNRESOLVED,
                    Severity.WARNING,
                    f"The relative {what} reference '{_shown(path)}' has no reference base "
                    "in its packages and could not be resolved.",
                    path=owner,
                    file=file,
                    line=line,
                )
                return None
            path = resolved
            tag = self.registry.get(path)
        if tag is None:
            self.add(
                codes.REF_UNRESOLVED,
                Severity.WARNING,
                f"The {what} reference to {path} could not be resolved: "
                "no loaded file defines this object.",
                path=owner,
                file=file,
                line=line,
            )
            return path
        if tag not in expected:
            self.add(
                codes.REF_WRONG_TYPE,
                Severity.WARNING,
                f"The {what} reference to {path} points to a {tag}, which cannot be used here.",
                path=owner,
                file=file,
                line=line,
            )
            return path
        if dest is not None and dest != tag and not _dest_matches(dest, tag):
            self.add(
                codes.REF_DEST_MISMATCH,
                Severity.WARNING,
                f'The {what} reference to {path} says DEST="{dest}" but the object is a {tag}.',
                path=owner,
                file=file,
                line=line,
            )
        return path

    # ---- pass 1: registration

    def admit(
        self, index: int, path: str, tag: str, line: int | None, owner: str | None = None
    ) -> int:
        """Register one object. Returns _NEW, _MERGED (split element) or _DROPPED."""
        if owner is not None and self.dropped and (index, owner) in self.dropped:
            self.dropped.add((index, path))
            return _DROPPED
        known = self.registry.get(path)
        if known is None:
            self.registry[path] = tag
            self.first_file[path] = index
            return _NEW
        first = self.first_file[path]
        if known == tag and tag in _SPLITABLE:
            files = self.splits.setdefault(path, [first])
            if index not in files:
                files.append(index)
            return _MERGED
        self.duplicates.append((path, first, index, known, tag, line))
        self.dropped.add((index, path))
        return _DROPPED

    def register(self) -> None:
        admit = self.admit
        skip = self.skip
        for index, rel, records in self.ordered:
            self.check_cancelled()
            for package, label, is_default, is_global, target, line in records.ref_bases:
                if target is None or not target.startswith("/"):
                    self.add(
                        codes.REF_RELATIVE_UNRESOLVED,
                        Severity.WARNING,
                        f"The reference base '{label}' has no absolute package reference "
                        "and is ignored.",
                        path=package,
                        file=rel,
                        line=line,
                    )
                    continue
                known = self.bases.setdefault(package, {})
                known.setdefault(label, target)
                if is_default:
                    known.setdefault("", target)
                if is_global:
                    self.global_bases.setdefault(label, target)
            for component in records.components:
                tag = component[2]
                if admit(index, component[0], tag, component[3]) != _NEW:
                    skip.add(id(component))
                elif tag not in self.component_tags:
                    self.component_tags.add(tag)
            for port in records.ports:
                if admit(index, port[0], port[3], port[6], port[2]) != _NEW:
                    skip.add(id(port))
            for behavior in records.behaviors:
                if admit(index, behavior[0], "SWC-INTERNAL-BEHAVIOR", behavior[3], behavior[2]) != (
                    _NEW
                ):
                    skip.add(id(behavior))
            for runnable in records.runnables:
                if admit(index, runnable[0], "RUNNABLE-ENTITY", runnable[7], runnable[2]) != _NEW:
                    skip.add(id(runnable))
            for access in records.accesses:
                kind = access[4]
                tag = "VARIABLE-ACCESS" if kind in _VARIABLE_ACCESS_LISTS else kind
                if admit(index, access[0], tag, access[7], access[2]) != _NEW:
                    skip.add(id(access))
            for event in records.events:
                tag = event[4]
                if admit(index, event[0], tag, event[11], event[2]) != _NEW:
                    skip.add(id(event))
                else:
                    self.event_tags.add(tag)
            for prototype in records.prototypes:
                if (
                    admit(index, prototype[0], "SW-COMPONENT-PROTOTYPE", prototype[4], prototype[2])
                    != _NEW
                ):
                    skip.add(id(prototype))
            for connector in records.connectors:
                if admit(index, connector[0], connector[3], connector[8], connector[2]) != _NEW:
                    skip.add(id(connector))
            for interface in records.interfaces:
                path = interface[0]
                line = interface[7]
                if admit(index, path, interface[2], line) != _NEW:
                    skip.add(id(interface))
                    continue
                for name, tag, _type, is_queued, _init in interface[4]:
                    element_path = f"{path}/{name}"
                    admit(index, element_path, tag, line)
                    if is_queued:
                        self.queued_elements.add(element_path)
                for operation in interface[5]:
                    admit(index, f"{path}/{operation[0]}", "CLIENT-SERVER-OPERATION", line)
                for error in interface[6]:
                    admit(index, f"{path}/{error}", "APPLICATION-ERROR", line)
            for data_type in records.data_types:
                if admit(index, data_type[0], data_type[2], data_type[12]) != _NEW:
                    skip.add(id(data_type))
            for base_type in records.base_types:
                if admit(index, base_type[0], "SW-BASE-TYPE", base_type[4]) != _NEW:
                    skip.add(id(base_type))
            for method in records.compu_methods:
                if admit(index, method[0], "COMPU-METHOD", method[7]) != _NEW:
                    skip.add(id(method))
            for constraint in records.constraints:
                if admit(index, constraint[0], "DATA-CONSTR", constraint[4]) != _NEW:
                    skip.add(id(constraint))
            for unit in records.units:
                if admit(index, unit[0], "UNIT", unit[3]) != _NEW:
                    skip.add(id(unit))
            for mapping_set in records.mapping_sets:
                if admit(index, mapping_set[0], "DATA-TYPE-MAPPING-SET", mapping_set[3]) != _NEW:
                    skip.add(id(mapping_set))
            for constant in records.constants:
                if admit(index, constant[0], "CONSTANT-SPECIFICATION", constant[3]) != _NEW:
                    skip.add(id(constant))
            for group in records.mode_groups:
                path = group[0]
                if admit(index, path, "MODE-DECLARATION-GROUP", group[3]) != _NEW:
                    continue
                for mode in group[2]:
                    admit(index, f"{path}/{mode}", "MODE-DECLARATION", group[3])

    def report_duplicates(self) -> None:
        """One finding per duplicated object; children of a reported object stay silent."""
        if not self.duplicates and not self.splits:
            return
        below_duplicate: set[str] = set()
        for path, *_rest in self.duplicates:
            parent = path.rpartition("/")[0]
            while parent and parent not in below_duplicate:
                below_duplicate.add(parent)
                parent = parent.rpartition("/")[0]
        reported: set[str] = set()
        for path, indexes in sorted(self.splits.items()):
            names = ", ".join(self.rel_of[i] for i in indexes)
            if path in below_duplicate:
                reported.add(path)
                self.add(
                    codes.DUPLICATE_PATH,
                    Severity.ERROR,
                    f"The {self.registry[path]} {path} is defined more than once with "
                    f"overlapping content ({names}). The first definition of each object wins.",
                    path=path,
                    file=self.rel_of[indexes[0]],
                )
            else:
                self.add(
                    codes.SPLIT_ELEMENT,
                    Severity.INFO,
                    f"The {self.registry[path]} {path} is split over several definitions "
                    f"({names}). They were merged.",
                    path=path,
                    file=self.rel_of[indexes[0]],
                )
        for path, first, second, first_tag, second_tag, line in self.duplicates:
            parent = path.rpartition("/")[0]
            silent = False
            while parent:
                if parent in reported:
                    silent = True
                    break
                parent = parent.rpartition("/")[0]
            if silent:
                continue
            reported.add(path)
            what = first_tag if first_tag == second_tag else f"{first_tag} / {second_tag}"
            self.add(
                codes.DUPLICATE_PATH,
                Severity.ERROR,
                f"The short-name path {path} ({what}) is defined in {self.rel_of[first]} "
                f"and again in {self.rel_of[second]}. The first definition is used.",
                path=path,
                file=self.rel_of[second],
                line=line,
            )

    def report_case_collisions(self) -> None:
        registry = self.registry
        if len({path.lower() for path in registry}) == len(registry):
            return
        by_lower: dict[str, list[str]] = {}
        for path in registry:
            by_lower.setdefault(path.lower(), []).append(path)
        reported: set[tuple[str, ...]] = set()
        for group in by_lower.values():
            if len(group) < 2:
                continue
            group.sort()
            # Report the names that differ (a package or an object), once, and not
            # every object below them.
            parts = [path.split("/") for path in group]
            depth = next(
                i for i in range(len(parts[0])) if len({segments[i] for segments in parts}) > 1
            )
            names = tuple(sorted({"/".join(segments[: depth + 1]) for segments in parts}))
            if names in reported:
                continue
            reported.add(names)
            self.add(
                codes.NAME_CASE,
                Severity.WARNING,
                "Short names that differ only by letter case: " + ", ".join(names) + ". "
                "They are separate objects in the model, but tools and file systems that "
                "ignore case will confuse them.",
                path=names[0],
                file=self.rel_of[self.first_file[group[0]]],
            )

    # ---- pass 2: objects

    def build(self) -> Model:
        self.file_findings()
        self.register()
        self.report_duplicates()
        self.report_case_collisions()
        self.check_cancelled()
        base_types, compu_methods, constraints, units = self.simple_types()
        data_types = self.data_types()
        mapping_sets, constants = self.mapping_sets_and_constants()
        self.check_cancelled()
        interfaces = self.interfaces()
        components, compositions = self.components()
        ports = self.ports()
        runnables = self.runnables()
        self.check_cancelled()
        # access points need the queued com specs of every port, triggers need access points
        access_points = self.access_points()
        triggers = self.triggers(access_points)
        self.check_cancelled()
        prototypes = self.prototypes()
        connectors = self.connectors()
        tasks, task_mappings = self.ecuc()
        self.report_schemas()
        self.check_cancelled()
        return Model(
            components=components,
            ports=ports,
            interfaces=interfaces,
            runnables=runnables,
            access_points=access_points,
            triggers=triggers,
            task_mappings=task_mappings,
            tasks=tasks,
            compositions=compositions,
            prototypes=prototypes,
            connectors=connectors,
            data_types=data_types,
            base_types=base_types,
            compu_methods=compu_methods,
            constraints=constraints,
            units=units,
            mapping_sets=mapping_sets,
            constants=constants,
            files=self.source_files(),
            findings=self.findings,
        )

    def simple_types(
        self,
    ) -> tuple[list[BaseType], list[CompuMethod], list[DataConstraint], list[Unit]]:
        skip = self.skip
        base_types: list[BaseType] = []
        compu_methods: list[CompuMethod] = []
        constraints: list[DataConstraint] = []
        units: list[Unit] = []
        for _index, rel, records in self.ordered:
            for base_type in records.base_types:
                if skip and id(base_type) in skip:
                    continue
                path, name, size_bits, encoding, line, uuid = base_type
                base_types.append(
                    BaseType(path, name, size_bits, encoding, Origin(rel, line, uuid))
                )
            for method in records.compu_methods:
                if skip and id(method) in skip:
                    continue
                path, name, category, factor, offset, enum_values, unit_ref, line, uuid = method
                compu_methods.append(
                    CompuMethod(
                        path,
                        name,
                        category,
                        factor,
                        offset,
                        enum_values,
                        self.resolve(unit_ref, _UNIT_TAGS, "unit", path, rel, line),
                        Origin(rel, line, uuid),
                    )
                )
            for constraint in records.constraints:
                if skip and id(constraint) in skip:
                    continue
                path, name, lower, upper, line, uuid = constraint
                constraints.append(
                    DataConstraint(path, name, lower, upper, Origin(rel, line, uuid))
                )
            for unit in records.units:
                if skip and id(unit) in skip:
                    continue
                path, name, display_name, line, uuid = unit
                units.append(Unit(path, name, display_name, Origin(rel, line, uuid)))
        return base_types, compu_methods, constraints, units

    def data_types(self) -> list[DataType]:
        skip = self.skip
        resolve = self.resolve
        encodings: dict[str, str | None] = {}
        compu_categories: dict[str, str | None] = {}
        for _index, _rel, records in self.ordered:
            for base_type in records.base_types:
                encodings.setdefault(base_type[0], base_type[3])
            for method in records.compu_methods:
                compu_categories.setdefault(method[0], method[2])
        result: list[DataType] = []
        for _index, rel, records in self.ordered:
            for data_type in records.data_types:
                if skip and id(data_type) in skip:
                    continue
                (
                    path,
                    name,
                    tag,
                    category,
                    base_ref,
                    compu_ref,
                    constraint_ref,
                    unit_ref,
                    type_ref,
                    element_names,
                    element_refs,
                    array_size,
                    line,
                    uuid,
                ) = data_type
                base_path = resolve(base_ref, _BASE_TYPE_TAGS, "base type", path, rel, line)
                compu_path = resolve(compu_ref, _COMPU_TAGS, "compu method", path, rel, line)
                is_implementation = tag == "IMPLEMENTATION-DATA-TYPE"
                expected = _ELEMENT_TYPE_TAGS if is_implementation else _APPLICATION_TAGS
                element_paths = [
                    resolve(ref, expected, "element type", path, rel, line) for ref in element_refs
                ]
                if type_ref is not None:
                    # A type reference has no elements: its target takes the first slot.
                    element_names = ()
                    element_paths = [
                        resolve(type_ref, _IMPLEMENTATION_TAGS, "data type", path, rel, line)
                    ]
                result.append(
                    DataType(
                        path=path,
                        name=name,
                        level=(
                            DataTypeLevel.IMPLEMENTATION
                            if is_implementation
                            else DataTypeLevel.APPLICATION
                        ),
                        category=_data_type_category(
                            tag,
                            category,
                            None if compu_path is None else compu_categories.get(compu_path),
                            None if base_path is None else encodings.get(base_path),
                        ),
                        base_type_path=base_path,
                        compu_method_path=compu_path,
                        constraint_path=resolve(
                            constraint_ref, _CONSTRAINT_TAGS, "data constraint", path, rel, line
                        ),
                        unit_path=resolve(unit_ref, _UNIT_TAGS, "unit", path, rel, line),
                        element_type_paths=tuple(p or "" for p in element_paths),
                        element_names=element_names,
                        array_size=array_size,
                        origin=Origin(rel, line, uuid),
                    )
                )
        return result

    def mapping_sets_and_constants(self) -> tuple[list[DataTypeMappingSet], list[Constant]]:
        skip = self.skip
        resolve = self.resolve
        mapping_sets: list[DataTypeMappingSet] = []
        constants: list[Constant] = []
        for _index, rel, records in self.ordered:
            for mapping_set in records.mapping_sets:
                if skip and id(mapping_set) in skip:
                    continue
                path, name, raw_pairs, line, uuid = mapping_set
                pairs: list[tuple[str, str]] = []
                for application_ref, implementation_ref in raw_pairs:
                    application = resolve(
                        application_ref, _APPLICATION_TAGS, "application data type", path, rel, line
                    )
                    implementation = resolve(
                        implementation_ref,
                        _IMPLEMENTATION_TAGS,
                        "implementation data type",
                        path,
                        rel,
                        line,
                    )
                    if application is not None and implementation is not None:
                        pairs.append((application, implementation))
                mapping_sets.append(
                    DataTypeMappingSet(path, name, tuple(pairs), Origin(rel, line, uuid))
                )
            for constant in records.constants:
                if skip and id(constant) in skip:
                    continue
                path, name, value, line, uuid = constant
                constants.append(Constant(path, name, value, Origin(rel, line, uuid)))
        return mapping_sets, constants

    def interfaces(self) -> list[PortInterface]:
        skip = self.skip
        resolve = self.resolve
        result: list[PortInterface] = []
        for _index, rel, records in self.ordered:
            for interface in records.interfaces:
                if skip and id(interface) in skip:
                    continue
                path, name, tag, is_service, raw_elements, raw_operations, _errors, line, uuid = (
                    interface
                )
                mode_group_path: str | None = None
                elements: list[DataElement] = []
                for element_name, element_tag, type_ref, is_queued, init in raw_elements:
                    if element_tag == "MODE-DECLARATION-GROUP-PROTOTYPE":
                        type_path = resolve(
                            type_ref, _MODE_GROUP_TAGS, "mode declaration group", path, rel, line
                        )
                        if mode_group_path is None:
                            mode_group_path = type_path
                    else:
                        type_path = resolve(type_ref, _DATA_TYPE_TAGS, "data type", path, rel, line)
                    elements.append(DataElement(element_name, type_path, is_queued, init))
                operations: list[Operation] = []
                for operation_name, raw_arguments, error_refs in raw_operations:
                    arguments = tuple(
                        Argument(
                            argument_name,
                            _ARGUMENT_DIRECTIONS.get(direction or "IN", ArgumentDirection.IN),
                            resolve(
                                argument_type, _DATA_TYPE_TAGS, "argument type", path, rel, line
                            ),
                        )
                        for argument_name, direction, argument_type in raw_arguments
                    )
                    errors = tuple(
                        _last(resolved)
                        for resolved in (
                            resolve(ref, _ERROR_TAGS, "possible error", path, rel, line)
                            for ref in error_refs
                        )
                        if resolved is not None
                    )
                    operations.append(Operation(operation_name, arguments, errors))
                result.append(
                    PortInterface(
                        path=path,
                        name=name,
                        kind=_INTERFACE_KINDS[tag],
                        is_service=is_service,
                        elements=tuple(elements),
                        operations=tuple(operations),
                        mode_group_path=mode_group_path,
                        origin=Origin(rel, line, uuid),
                    )
                )
        return result

    def components(self) -> tuple[list[Component], list[Composition]]:
        skip = self.skip
        config = self.config
        behavior_of: dict[str, str] = {}
        for _index, rel, records in self.ordered:
            for behavior in records.behaviors:
                if skip and id(behavior) in skip:
                    continue
                path, _name, swc_path, line, _uuid = behavior
                known = behavior_of.setdefault(swc_path, path)
                if known != path:
                    self.add(
                        codes.BEHAVIOR_MULTIPLE,
                        Severity.WARNING,
                        f"The component has more than one internal behavior. {known} is "
                        f"used as its behavior; runnables of {path} are still loaded.",
                        path=swc_path,
                        file=rel,
                        line=line,
                    )
        components: list[Component] = []
        compositions: list[Composition] = []
        for _index, rel, records in self.ordered:
            if not records.components:
                continue
            generated = self.is_generated(rel)
            for component in records.components:
                if skip and id(component) in skip:
                    continue
                path, name, tag, line, uuid = component
                kind = _COMPONENT_KINDS.get(tag)
                if kind is None:
                    kind = ComponentKind.OTHER
                    self.add(
                        codes.KIND_UNKNOWN,
                        Severity.WARNING,
                        f"Unknown software component kind {tag}. The component is loaded as "
                        "'other'; its ports are shown but the kind is not supported.",
                        path=path,
                        file=rel,
                        line=line,
                    )
                owner = config.owner_of(name)
                if owner is not ArxmlOwner.LOCKED and (kind is ComponentKind.SERVICE or generated):
                    owner = ArxmlOwner.LOCKED
                origin = Origin(rel, line, uuid)
                components.append(
                    Component(
                        path=path,
                        name=name,
                        kind=kind,
                        owner=owner,
                        team=config.team_of(name),
                        behavior_path=behavior_of.get(path),
                        origin=origin,
                    )
                )
                if kind is ComponentKind.COMPOSITION:
                    compositions.append(Composition(path, name, origin))
        return components, compositions

    def ports(self) -> list[Port]:
        skip = self.skip
        resolve = self.resolve
        result: list[Port] = []
        add = result.append
        for _index, rel, records in self.ordered:
            for port in records.ports:
                if skip and id(port) in skip:
                    continue
                path, name, swc_path, tag, interface_ref, raw_specs, line, uuid = port
                com_specs: tuple[ComSpec, ...] = ()
                if raw_specs:
                    specs: list[ComSpec] = []
                    for spec_tag, target_ref, init, queue_length in raw_specs:
                        target_path = resolve(
                            target_ref, _TARGET_TAGS, "com spec target", path, rel, line
                        )
                        target = "" if target_path is None else _last(target_path)
                        if spec_tag == "QUEUED-RECEIVER-COM-SPEC":
                            self.queued_ports.add((path, target))
                        specs.append(ComSpec(target, init, queue_length))
                    com_specs = tuple(specs)
                add(
                    Port(
                        path,
                        name,
                        swc_path,
                        _PORT_DIRECTIONS[tag],
                        resolve(interface_ref, _INTERFACE_TAGS, "interface", path, rel, line),
                        com_specs,
                        Origin(rel, line, uuid),
                    )
                )
        return result

    def runnables(self) -> list[Runnable]:
        skip = self.skip
        result: list[Runnable] = []
        for _index, rel, records in self.ordered:
            for runnable in records.runnables:
                if skip and id(runnable) in skip:
                    continue
                path, name, _behavior, swc_path, symbol, concurrent, interval, line, uuid = runnable
                result.append(
                    Runnable(
                        path, name, swc_path, symbol, concurrent, interval, Origin(rel, line, uuid)
                    )
                )
        return result

    def access_points(self) -> list[AccessPoint]:
        skip = self.skip
        resolve = self.resolve
        queued_elements = self.queued_elements
        queued_ports = self.queued_ports
        result: list[AccessPoint] = []
        add = result.append
        for _index, rel, records in self.ordered:
            for access in records.accesses:
                if skip and id(access) in skip:
                    continue
                path, _name, runnable_path, swc_path, tag, port_ref, target_ref, line, uuid = access
                port_path = resolve(port_ref, _PORT_TAGS, "port", path, rel, line)
                target_path = resolve(target_ref, _TARGET_TAGS, "access target", path, rel, line)
                target = None if target_path is None else _last(target_path)
                kind = _ACCESS_KINDS.get(tag, AccessKind.OTHER)
                if kind is AccessKind.EXPLICIT_READ and (
                    target_path in queued_elements or (port_path, target) in queued_ports
                ):
                    kind = AccessKind.QUEUED_RECEIVE
                add(
                    AccessPoint(
                        path,
                        swc_path,
                        runnable_path,
                        port_path,
                        target,
                        kind,
                        Origin(rel, line, uuid),
                    )
                )
        return result

    def triggers(self, access_points: Sequence[AccessPoint]) -> list[Trigger]:
        skip = self.skip
        resolve = self.resolve
        access_by_path: dict[str, AccessPoint] | None = None
        result: list[Trigger] = []
        for _index, rel, records in self.ordered:
            for event in records.events:
                if skip and id(event) in skip:
                    continue
                (
                    path,
                    name,
                    _behavior,
                    swc_path,
                    tag,
                    runnable_ref,
                    port_ref,
                    target_ref,
                    target2_ref,
                    period,
                    source_ref,
                    line,
                    uuid,
                ) = event
                kind = _TRIGGER_KINDS.get(tag, TriggerKind.OTHER)
                port_path = resolve(port_ref, _PORT_TAGS, "port", path, rel, line)
                target_path = resolve(
                    target_ref, _EVENT_TARGET_TAGS, "event target", path, rel, line
                )
                target = None if target_path is None else _last(target_path)
                if target2_ref is not None:
                    second = resolve(
                        target2_ref, _EVENT_TARGET_TAGS, "event target", path, rel, line
                    )
                    if second is not None:
                        target = f"{target or '?'}->{_last(second)}"
                if kind is TriggerKind.DATA_SEND_COMPLETED and source_ref is not None:
                    source = resolve(
                        source_ref, _VARIABLE_ACCESS_TAGS, "event source", path, rel, line
                    )
                    if access_by_path is None:
                        access_by_path = {point.path: point for point in access_points}
                    sender = None if source is None else access_by_path.get(source)
                    if sender is not None:
                        port_path = sender.port_path
                        target = sender.target
                result.append(
                    Trigger(
                        path=path,
                        name=name,
                        swc_path=swc_path,
                        runnable_path=resolve(
                            runnable_ref, _RUNNABLE_TAGS, "runnable", path, rel, line
                        ),
                        kind=kind,
                        port_path=port_path,
                        target=target,
                        period_s=period,
                        origin=Origin(rel, line, uuid),
                    )
                )
        return result

    def prototypes(self) -> list[ComponentPrototype]:
        skip = self.skip
        result: list[ComponentPrototype] = []
        for _index, rel, records in self.ordered:
            for prototype in records.prototypes:
                if skip and id(prototype) in skip:
                    continue
                path, name, composition_path, type_ref, line, uuid = prototype
                result.append(
                    ComponentPrototype(
                        path,
                        name,
                        composition_path,
                        self.resolve(
                            type_ref, self.component_tags, "component type", path, rel, line
                        ),
                        Origin(rel, line, uuid),
                    )
                )
        return result

    def connectors(self) -> list[Connector]:
        skip = self.skip
        resolve = self.resolve
        result: list[Connector] = []
        add = result.append
        for _index, rel, records in self.ordered:
            for connector in records.connectors:
                if skip and id(connector) in skip:
                    continue
                (
                    path,
                    name,
                    composition_path,
                    tag,
                    provider_component,
                    provider_port,
                    requester_component,
                    requester_port,
                    line,
                    uuid,
                ) = connector
                kind = _CONNECTOR_KINDS.get(tag)
                if kind is None:
                    self.add(
                        codes.CONNECTOR_UNSUPPORTED,
                        Severity.WARNING,
                        f"Connectors of kind {tag} are not supported and are missing from the "
                        "model.",
                        path=path,
                        file=rel,
                        line=line,
                    )
                    continue
                add(
                    Connector(
                        path,
                        name,
                        composition_path,
                        kind,
                        ConnectorEnd(
                            resolve(
                                provider_component,
                                _PROTOTYPE_TAGS,
                                "provider component",
                                path,
                                rel,
                                line,
                            ),
                            resolve(provider_port, _PORT_TAGS, "provider port", path, rel, line),
                        ),
                        ConnectorEnd(
                            resolve(
                                requester_component,
                                _PROTOTYPE_TAGS,
                                "requester component",
                                path,
                                rel,
                                line,
                            ),
                            resolve(requester_port, _PORT_TAGS, "requester port", path, rel, line),
                        ),
                        Origin(rel, line, uuid),
                    )
                )
        return result

    # ---- ECUC

    def ecuc(self) -> tuple[list[str], list[TaskMapping]]:
        layout = self.layout
        ecuc_files = {f.rel_path for f in self.discovered if f.is_ecuc}
        tasks: dict[str, None] = {}
        raw: list[tuple[str, str, int | None, str, str, int | None]] = []
        for _index, rel, records in self.ordered:
            if rel not in ecuc_files:
                continue
            for path, name, definition, params, refs, line, _uuid in records.ecuc_containers:
                if definition.endswith(layout.os_task):
                    tasks.setdefault(name, None)
                elif definition.endswith(layout.event_mapping):
                    event: str | None = None
                    task_ref: str | None = None
                    for ref_definition, value, _dest in refs:
                        if ref_definition.endswith(layout.event_ref):
                            event = value
                        elif ref_definition.endswith(layout.task_ref):
                            task_ref = value
                    if event is None or task_ref is None:
                        continue  # the event is not mapped to a task
                    position: int | None = None
                    for param_definition, value in params:
                        if param_definition.endswith(layout.position):
                            position = parse_int(value)
                    raw.append((event, _last(task_ref), position, path, rel, line))
        mappings: dict[str, TaskMapping] = {}
        event_tags = self.event_tags
        for event, task, position, path, rel, line in raw:
            known = mappings.get(event)
            if known is not None:
                if known.task != task:
                    self.add(
                        codes.ECUC_MAPPING_CONFLICT,
                        Severity.WARNING,
                        f"The event {event} is mapped to task {known.task} and, for another "
                        f"component instance, to task {task}. The model shows {known.task}.",
                        path=event,
                        file=rel,
                        line=line,
                    )
                continue
            mappings[event] = TaskMapping(event, task, position)
            if self.registry.get(event) not in event_tags:
                self.add(
                    codes.ECUC_EVENT_UNKNOWN,
                    Severity.WARNING,
                    f"The task mapping {path} refers to the event {event}, which is not in "
                    "the loaded files.",
                    path=event,
                    file=rel,
                    line=line,
                )
            if task not in tasks:
                self.add(
                    codes.ECUC_TASK_UNKNOWN,
                    Severity.WARNING,
                    f"The task mapping {path} refers to the task {task}, which is not an OS "
                    "task in the ECUC file.",
                    path=event,
                    file=rel,
                    line=line,
                )
        return list(tasks), list(mappings.values())

    # ---- files

    def report_schemas(self) -> None:
        by_schema: dict[str, list[str]] = {}
        for _index, rel, records in self.ordered:
            if records.status == STATUS_OK and records.schema is not None:
                by_schema.setdefault(records.schema, []).append(rel)
        if len(by_schema) < 2:
            return
        ranked = sorted(by_schema.items(), key=lambda item: (-len(item[1]), item[0]))
        summary = ", ".join(f"{schema} ({len(files)} files)" for schema, files in ranked)
        minority = [rel for _schema, files in ranked[1:] for rel in files]
        examples = ", ".join(minority[:5]) + (", ..." if len(minority) > 5 else "")
        self.add(
            codes.SCHEMA_MIXED,
            Severity.WARNING,
            f"The loaded files use more than one AUTOSAR schema version: {summary}. "
            f"Files not using {ranked[0][0]}: {examples}.",
            file=minority[0],
        )

    def source_files(self) -> list[SourceFile]:
        """Fingerprints of what was loaded. A file that vanished during the load is left out."""
        result: list[SourceFile] = []
        for found in self.discovered:
            records = self.records.get(found.rel_path)
            if records is None or records.status == STATUS_VANISHED:
                continue
            if records.status in _NOT_READ:
                # the file could not be read: keep the fingerprint the scan saw
                result.append(SourceFile(found.rel_path, found.size, found.mtime_ns))
            else:
                result.append(
                    SourceFile(found.rel_path, records.size, records.mtime_ns, records.sha256)
                )
        result.extend(self.extra_files)
        return result


def _data_type_category(
    tag: str, category: str | None, compu_category: str | None, encoding: str | None
) -> DataTypeCategory:
    text_table = compu_category is not None and "TEXTTABLE" in compu_category.upper()
    category = (category or "").upper()
    if tag == "APPLICATION-ARRAY-DATA-TYPE":
        return DataTypeCategory.ARRAY
    if tag == "APPLICATION-RECORD-DATA-TYPE":
        return DataTypeCategory.RECORD
    if category == "ARRAY":
        return DataTypeCategory.ARRAY
    if category == "STRUCTURE":
        return DataTypeCategory.RECORD
    if category == "TYPE_REFERENCE":
        return DataTypeCategory.TYPE_REFERENCE
    if category == "BOOLEAN":
        return DataTypeCategory.BOOLEAN
    if category == "STRING":
        return DataTypeCategory.STRING
    if category == "VALUE":
        if encoding is not None and encoding.upper() == "BOOLEAN":
            return DataTypeCategory.BOOLEAN
        return DataTypeCategory.ENUMERATION if text_table else DataTypeCategory.VALUE
    return DataTypeCategory.OTHER


def build_model(
    config: ProjectConfig,
    files: Sequence[DiscoveredFile],
    records: Mapping[str, FileRecords],
    *,
    extra_files: Sequence[SourceFile] = (),
    findings: Sequence[Finding] = (),
    layout: EcucLayout | None = None,
    generated_globs: Sequence[str] = (),
    check_cancelled: CheckCancelled | None = None,
) -> Model:
    """Build the model. ``findings`` (from discovery and the cache) come first in the result."""
    builder = _Builder(
        config,
        files,
        records,
        layout or EcucLayout(),
        generated_globs,
        check_cancelled or (lambda: None),
        extra_files,
    )
    builder.findings.extend(findings)
    return builder.build()
