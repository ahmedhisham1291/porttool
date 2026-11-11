"""GroundTruth: what a correct reader must find in a generated repo.

The objects are the contract types of ``porttool.core.models``, so the truth can be
compared with a reader's ``Model`` field by field, or turned into a ``Model`` directly
(``to_model``) for packages that need a big model without a reader.

Exact by the AUTOSAR standard: paths, names, kinds, directions, references, periods,
task mappings, connectors, unconnected port instances. By this generator's convention
(a reader may legitimately differ): ``Origin.line`` is None, init values are the text
of the VALUE element, ``Operation.possible_errors`` holds error short names, a VALUE
type with a TEXTTABLE compu method has category ENUMERATION, ``DataType.unit_path`` is
None (the unit hangs on the compu method).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from porttool.core.models import (
    AccessPoint,
    Argument,
    ArgumentDirection,
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
    DataConstraint,
    DataElement,
    DataType,
    DataTypeCategory,
    DataTypeLevel,
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
    Unit,
)
from tests.fixtures.generator import plan as p
from tests.fixtures.generator.spec import GeneratorSpec

_CATEGORIES = {
    p.VALUE: DataTypeCategory.VALUE,
    p.ENUM: DataTypeCategory.ENUMERATION,
    p.ARRAY: DataTypeCategory.ARRAY,
    p.RECORD: DataTypeCategory.RECORD,
}


@dataclass(frozen=True, slots=True)
class GroundTruth:
    spec: GeneratorSpec
    repo_root: Path | None  # None when only rendered in memory
    files: tuple[str, ...]  # every generated file, repo-relative, sorted
    arxml_root: str  # the folder a reader loads
    arxml_files: tuple[str, ...]  # the workspace ARXML files below arxml_root, sorted
    ecuc_file: str
    dcf_file: str
    dpa_file: str
    total_bytes: int
    components: tuple[Component, ...]  # atomic SWC types and the composition type
    ports: tuple[Port, ...]
    interfaces: tuple[PortInterface, ...]
    runnables: tuple[Runnable, ...]
    access_points: tuple[AccessPoint, ...]
    triggers: tuple[Trigger, ...]
    task_mappings: tuple[TaskMapping, ...]
    tasks: tuple[str, ...]
    compositions: tuple[Composition, ...]
    prototypes: tuple[ComponentPrototype, ...]
    connectors: tuple[Connector, ...]
    data_types: tuple[DataType, ...]
    base_types: tuple[BaseType, ...]
    compu_methods: tuple[CompuMethod, ...]
    constraints: tuple[DataConstraint, ...]
    units: tuple[Unit, ...]
    #: Port instances without a connector: (prototype path, port path), sorted.
    unconnected: tuple[tuple[str, str], ...]
    model_owned_swcs: tuple[str, ...]  # SWC short names
    locked_swcs: tuple[str, ...]
    source_files: tuple[SourceFile, ...]  # size of every arxml_files entry and the ECUC file

    # ---- name lists (absolute AUTOSAR paths, in file order)

    @property
    def swc_paths(self) -> tuple[str, ...]:
        """Atomic SWC types, without the composition."""
        return tuple(c.path for c in self.components if c.kind is not ComponentKind.COMPOSITION)

    @property
    def port_paths(self) -> tuple[str, ...]:
        return tuple(port.path for port in self.ports)

    @property
    def runnable_paths(self) -> tuple[str, ...]:
        return tuple(runnable.path for runnable in self.runnables)

    @property
    def access_point_paths(self) -> tuple[str, ...]:
        return tuple(access.path for access in self.access_points)

    @property
    def trigger_paths(self) -> tuple[str, ...]:
        return tuple(trigger.path for trigger in self.triggers)

    @property
    def connector_paths(self) -> tuple[str, ...]:
        return tuple(connector.path for connector in self.connectors)

    @property
    def counts(self) -> Mapping[str, int]:
        return {
            "swcs": len(self.swc_paths),
            "model_owned_swcs": len(self.model_owned_swcs),
            "locked_swcs": len(self.locked_swcs),
            "ports": len(self.ports),
            "p_ports": sum(1 for port in self.ports if port.direction is PortDirection.P),
            "r_ports": sum(1 for port in self.ports if port.direction is PortDirection.R),
            "interfaces": len(self.interfaces),
            "data_types": len(self.data_types),
            "runnables": len(self.runnables),
            "access_points": len(self.access_points),
            "triggers": len(self.triggers),
            "task_mappings": len(self.task_mappings),
            "prototypes": len(self.prototypes),
            "connectors": len(self.connectors),
            "unconnected": len(self.unconnected),
            "files": len(self.files),
            "arxml_files": len(self.arxml_files),
            "bytes": self.total_bytes,
        }

    def to_model(self) -> Model:
        """The ``Model`` a perfect reader would build from the generated repo."""
        return Model(
            components=self.components,
            ports=self.ports,
            interfaces=self.interfaces,
            runnables=self.runnables,
            access_points=self.access_points,
            triggers=self.triggers,
            task_mappings=self.task_mappings,
            tasks=self.tasks,
            compositions=self.compositions,
            prototypes=self.prototypes,
            connectors=self.connectors,
            data_types=self.data_types,
            base_types=self.base_types,
            compu_methods=self.compu_methods,
            constraints=self.constraints,
            units=self.units,
            files=self.source_files,
        )


def _port(port: p.PortPlan, origin: Origin) -> Port:
    interface = port.interface
    assert interface is not None
    specs: tuple[ComSpec, ...] = ()
    if not interface.client_server:
        specs = tuple(ComSpec(e.name, init_value=e.init_value) for e in interface.elements)
    elif port.provider:
        specs = tuple(ComSpec(o.name, queue_length=1) for o in interface.operations)
    return Port(
        path=port.path,
        name=port.name,
        swc_path=port.swc.path,
        direction=PortDirection.P if port.provider else PortDirection.R,
        interface_path=interface.path,
        com_specs=specs,
        origin=Origin(origin.file, uuid=port.uuid),
    )


def build_truth(
    plan: p.Plan, files: Mapping[str, bytes], arxml_root: str, ecuc_file: str, dcf: str, dpa: str
) -> GroundTruth:
    """Derive the ground truth from the plan and the rendered files."""
    composition_file = f"{arxml_root}/ComponentTypes/{p.COMPOSITION_NAME}.arxml"
    components: list[Component] = []
    ports: list[Port] = []
    runnables: list[Runnable] = []
    access_points: list[AccessPoint] = []
    triggers: list[Trigger] = []
    task_mappings: list[TaskMapping] = []
    prototypes: list[ComponentPrototype] = []
    for swc in plan.swcs:
        origin = Origin(swc.file)
        behavior = swc.behavior_path
        components.append(
            Component(
                path=swc.path,
                name=swc.name,
                kind=swc.kind,
                owner=swc.owner,
                team=swc.team,
                behavior_path=behavior,
                origin=Origin(swc.file, uuid=swc.uuid),
            )
        )
        prototypes.append(
            ComponentPrototype(
                path=swc.prototype_path,
                name=swc.name,
                composition_path=p.COMPOSITION,
                type_path=swc.path,
                origin=Origin(composition_file, uuid=swc.prototype_uuid),
            )
        )
        ports.extend(_port(port, origin) for port in swc.ports)
        for runnable in swc.runnables:
            runnable_path = f"{behavior}/{runnable.name}"
            runnables.append(
                Runnable(
                    path=runnable_path,
                    name=runnable.name,
                    swc_path=swc.path,
                    symbol=runnable.symbol,
                    can_be_invoked_concurrently=False,
                    min_start_interval_s=0.0,
                    origin=Origin(swc.file, uuid=runnable.uuid),
                )
            )
            access_points.extend(
                AccessPoint(
                    path=f"{runnable_path}/{access.name}",
                    swc_path=swc.path,
                    runnable_path=runnable_path,
                    port_path=access.port.path,
                    target=access.target,
                    kind=access.kind,
                    origin=Origin(swc.file, uuid=access.uuid),
                )
                for access in runnable.accesses
            )
        for event in swc.events:
            event_path = f"{behavior}/{event.name}"
            triggers.append(
                Trigger(
                    path=event_path,
                    name=event.name,
                    swc_path=swc.path,
                    runnable_path=f"{behavior}/{event.runnable.name}",
                    kind=event.kind,
                    port_path=event.port.path if event.port is not None else None,
                    target=event.target,
                    period_s=event.period_s,
                    origin=Origin(swc.file, uuid=event.uuid),
                )
            )
            if event.task is not None:
                task_mappings.append(TaskMapping(event_path, event.task, event.position))
    components.append(
        Component(
            path=p.COMPOSITION,
            name=p.COMPOSITION_NAME,
            kind=ComponentKind.COMPOSITION,
            origin=Origin(composition_file, uuid=plan.composition_uuid),
        )
    )
    connectors = tuple(
        Connector(
            path=connector.path,
            name=connector.name,
            composition_path=p.COMPOSITION,
            kind=ConnectorKind.ASSEMBLY,
            provider=ConnectorEnd(connector.provider.swc.prototype_path, connector.provider.path),
            requester=ConnectorEnd(
                connector.requester.swc.prototype_path, connector.requester.path
            ),
            origin=Origin(composition_file, uuid=connector.uuid),
        )
        for connector in plan.connectors
    )
    connected = {
        (end.prototype_path, end.port_path) for c in connectors for end in (c.provider, c.requester)
    }
    unconnected = sorted(
        (swc.prototype_path, port.path)
        for swc in plan.swcs
        for port in swc.ports
        if (swc.prototype_path, port.path) not in connected
    )
    interfaces = tuple(
        PortInterface(
            path=interface.path,
            name=interface.name,
            kind=InterfaceKind.CLIENT_SERVER
            if interface.client_server
            else InterfaceKind.SENDER_RECEIVER,
            elements=tuple(DataElement(e.name, e.data_type.path) for e in interface.elements),
            operations=tuple(
                Operation(
                    name=operation.name,
                    arguments=tuple(
                        Argument(a.name, ArgumentDirection(a.direction), a.data_type.path)
                        for a in operation.arguments
                    ),
                    possible_errors=operation.possible_errors,
                )
                for operation in interface.operations
            ),
            origin=Origin(interface.file, uuid=interface.uuid),
        )
        for interface in plan.interfaces
    )
    data_types: list[DataType] = []
    compu_methods: list[CompuMethod] = []
    constraints: list[DataConstraint] = []
    for data_type in plan.data_types:
        scalar = data_type.is_scalar
        elements = (
            (data_type.element,) if data_type.element else tuple(t for _, t in data_type.fields)
        )
        data_types.append(
            DataType(
                path=data_type.path,
                name=data_type.name,
                level=DataTypeLevel.IMPLEMENTATION,
                category=_CATEGORIES[data_type.category],
                base_type_path=data_type.base_type.path if data_type.base_type else None,
                compu_method_path=data_type.compu_path if scalar else None,
                constraint_path=data_type.constr_path if scalar else None,
                element_type_paths=tuple(element.path for element in elements),
                element_names=tuple(name for name, _ in data_type.fields),
                array_size=data_type.array_size or None,
                origin=Origin(data_type.file, uuid=data_type.uuid),
            )
        )
        if not scalar:
            continue
        linear = data_type.category == p.VALUE
        compu_methods.append(
            CompuMethod(
                path=data_type.compu_path,
                name=f"CM_{data_type.name}",
                category="LINEAR" if linear else "TEXTTABLE",
                factor=data_type.factor if linear else None,
                offset=data_type.offset if linear else None,
                enum_values=data_type.enum_values,
                unit_path=data_type.unit.path if data_type.unit else None,
                origin=Origin(data_type.file, uuid=data_type.compu_uuid),
            )
        )
        constraints.append(
            DataConstraint(
                path=data_type.constr_path,
                name=f"DC_{data_type.name}",
                lower=data_type.lower,
                upper=data_type.upper,
                origin=Origin(data_type.file, uuid=data_type.constr_uuid),
            )
        )
    base_file = f"{arxml_root}/DataTypes/BaseTypes.arxml"
    unit_file = f"{arxml_root}/DataTypes/Units.arxml"
    arxml_files = tuple(
        sorted(
            name for name in files if name.startswith(arxml_root + "/") and name.endswith(".arxml")
        )
    )
    return GroundTruth(
        spec=plan.spec,
        repo_root=None,
        files=tuple(sorted(files)),
        arxml_root=arxml_root,
        arxml_files=arxml_files,
        ecuc_file=ecuc_file,
        dcf_file=dcf,
        dpa_file=dpa,
        total_bytes=sum(len(data) for data in files.values()),
        components=tuple(components),
        ports=tuple(ports),
        interfaces=interfaces,
        runnables=tuple(runnables),
        access_points=tuple(access_points),
        triggers=tuple(triggers),
        task_mappings=tuple(task_mappings),
        tasks=plan.tasks,
        compositions=(Composition(p.COMPOSITION, p.COMPOSITION_NAME, Origin(composition_file)),),
        prototypes=tuple(prototypes),
        connectors=connectors,
        data_types=tuple(data_types),
        base_types=tuple(
            BaseType(b.path, b.name, b.size_bits, b.encoding, Origin(base_file, uuid=b.uuid))
            for b in plan.base_types
        ),
        compu_methods=tuple(compu_methods),
        constraints=tuple(constraints),
        units=tuple(
            Unit(u.path, u.name, u.display, Origin(unit_file, uuid=u.uuid)) for u in plan.units
        ),
        unconnected=tuple(unconnected),
        model_owned_swcs=tuple(s.name for s in plan.swcs if s.export_folder),
        locked_swcs=tuple(sorted(s.name for s in plan.swcs if s.owner.value == "locked")),
        source_files=tuple(
            SourceFile(rel_path=name, size=len(files[name]), mtime_ns=0)
            for name in (*arxml_files, ecuc_file)
        ),
    )
