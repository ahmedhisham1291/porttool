"""A large synthetic Model for the performance tests of the workbook package.

About 20,000 ports, 15,000 connectors and 15,000 access points: the workbook built
from it holds a little more than 55,000 rows. All names are made up.
"""

from __future__ import annotations

from porttool.core.models import (
    AccessKind,
    AccessPoint,
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

CT = "/ComponentTypes"
IF = "/PortInterfaces"
DT = "/DataTypes"
COMPOSITION = f"{CT}/EcuComposition"

SWC_COUNT = 400
SIGNAL_COUNT = 5_000
RECEIVERS_PER_SIGNAL = 3
INTERFACE_COUNT = 2_500
DATA_TYPE_COUNT = 300
RUNNABLES_PER_SWC = 4
ACCESS_POINT_COUNT = 15_000
SPARE_PORT_COUNT = 200
TASKS = tuple(f"Task_{period}ms" for period in (1, 2, 5, 10, 20, 50, 100, 1000))
_DOMAINS = ("Brk", "Eng", "Trm", "Chs", "Bdy", "Hvac", "Diag", "Nvm")


def _swc_name(index: int) -> str:
    return f"Swc{_DOMAINS[index % len(_DOMAINS)]}Ctrl{index:03d}"


def build_large_model() -> Model:
    origin_if = Origin(file="Config/Developer/PortInterfaces/Interfaces.arxml")
    origin_dt = Origin(file="Config/Developer/DataTypes/DataTypes.arxml")
    origin_c = Origin(file="Config/Developer/ComponentTypes/EcuComposition.arxml")

    base_types = [
        BaseType(path=f"{DT}/BaseTypes/{name}", name=name, size_bits=bits, origin=origin_dt)
        for name, bits in (("uint8", 8), ("uint16", 16), ("uint32", 32), ("sint16", 16))
    ]
    units = [
        Unit(path=f"{DT}/Units/{name}", name=name, origin=origin_dt)
        for name in ("km_h", "Nm", "rpm", "degC", "V", "A")
    ]
    compu_methods = []
    constraints = []
    data_types = []
    for index in range(DATA_TYPE_COUNT):
        name = f"Dt_Signal{index:04d}"
        compu_methods.append(
            CompuMethod(
                path=f"{DT}/CompuMethods/CM_{name}",
                name=f"CM_{name}",
                category="LINEAR",
                factor=0.01 * (1 + index % 7),
                offset=float(-(index % 3) * 40),
                unit_path=units[index % len(units)].path,
                origin=origin_dt,
            )
        )
        constraints.append(
            DataConstraint(
                path=f"{DT}/DataConstrs/DC_{name}",
                name=f"DC_{name}",
                lower=0.0,
                upper=float(2 ** (8 * (1 + index % 3)) - 1),
                origin=origin_dt,
            )
        )
        data_types.append(
            DataType(
                path=f"{DT}/{name}",
                name=name,
                level=DataTypeLevel.APPLICATION,
                category=DataTypeCategory.VALUE,
                base_type_path=base_types[index % len(base_types)].path,
                compu_method_path=compu_methods[-1].path,
                constraint_path=constraints[-1].path,
                origin=origin_dt,
            )
        )

    interfaces = [
        PortInterface(
            path=f"{IF}/If_Signal{index:04d}",
            name=f"If_Signal{index:04d}",
            kind=InterfaceKind.SENDER_RECEIVER,
            elements=(
                DataElement(
                    name=f"Signal{index:04d}",
                    type_path=data_types[index % DATA_TYPE_COUNT].path,
                ),
            ),
            origin=origin_if,
        )
        for index in range(INTERFACE_COUNT)
    ]

    components = []
    prototypes = []
    runnables = []
    triggers = []
    task_mappings = []
    files = [origin_if.file, origin_dt.file, origin_c.file]
    for index in range(SWC_COUNT):
        name = _swc_name(index)
        swc_path = f"{CT}/{name}"
        origin = Origin(file=f"Config/Developer/ComponentTypes/{name}.arxml")
        files.append(origin.file)
        behavior = f"{swc_path}/{name}_InternalBehavior"
        components.append(
            Component(
                path=swc_path,
                name=name,
                kind=ComponentKind.APPLICATION,
                team=f"Team{index % 12:02d}",
                behavior_path=behavior,
                origin=origin,
            )
        )
        prototypes.append(
            ComponentPrototype(
                path=f"{COMPOSITION}/{name}",
                name=name,
                composition_path=COMPOSITION,
                type_path=swc_path,
                origin=origin_c,
            )
        )
        for number in range(RUNNABLES_PER_SWC):
            task = TASKS[(index + number) % len(TASKS)]
            runnable_name = f"{name}_Run{number}_{task[5:]}"
            runnable_path = f"{behavior}/{runnable_name}"
            runnables.append(
                Runnable(
                    path=runnable_path,
                    name=runnable_name,
                    swc_path=swc_path,
                    symbol=runnable_name,
                    origin=origin,
                )
            )
            trigger_path = f"{behavior}/TMT_{runnable_name}"
            triggers.append(
                Trigger(
                    path=trigger_path,
                    name=f"TMT_{runnable_name}",
                    swc_path=swc_path,
                    runnable_path=runnable_path,
                    kind=TriggerKind.PERIODIC,
                    period_s=int(task[5:-2]) / 1000.0,
                    origin=origin,
                )
            )
            task_mappings.append(TaskMapping(event_path=trigger_path, task=task, position=number))
    components.append(
        Component(
            path=COMPOSITION,
            name="EcuComposition",
            kind=ComponentKind.COMPOSITION,
            origin=origin_c,
        )
    )

    ports: list[Port] = []
    connectors: list[Connector] = []

    def add_port(swc_index: int, prefix: str, signal: int, direction: PortDirection) -> Port:
        swc = _swc_name(swc_index)
        interface = interfaces[signal % INTERFACE_COUNT]
        port = Port(
            path=f"{CT}/{swc}/{prefix}_Signal{signal:04d}",
            name=f"{prefix}_Signal{signal:04d}",
            swc_path=f"{CT}/{swc}",
            direction=direction,
            interface_path=interface.path,
            com_specs=(ComSpec(target=interface.elements[0].name, init_value="0"),),
            origin=Origin(file=f"Config/Developer/ComponentTypes/{swc}.arxml"),
        )
        ports.append(port)
        return port

    for signal in range(SIGNAL_COUNT):
        provider_index = signal % SWC_COUNT
        provider = add_port(provider_index, "Pp", signal, PortDirection.P)
        for step in range(RECEIVERS_PER_SIGNAL):
            requester_index = (provider_index + 1 + (signal * 7 + step * 13) % (SWC_COUNT - 1)) % (
                SWC_COUNT
            )
            requester = add_port(requester_index, f"Rp{step}", signal, PortDirection.R)
            connector_name = (
                f"{_swc_name(provider_index)}_{provider.name}_"
                f"{_swc_name(requester_index)}_{requester.name}"
            )
            connectors.append(
                Connector(
                    path=f"{COMPOSITION}/{connector_name}",
                    name=connector_name,
                    composition_path=COMPOSITION,
                    kind=ConnectorKind.ASSEMBLY,
                    provider=ConnectorEnd(
                        prototype_path=f"{COMPOSITION}/{_swc_name(provider_index)}",
                        port_path=provider.path,
                    ),
                    requester=ConnectorEnd(
                        prototype_path=f"{COMPOSITION}/{_swc_name(requester_index)}",
                        port_path=requester.path,
                    ),
                    origin=origin_c,
                )
            )
    for spare in range(SPARE_PORT_COUNT):
        add_port(spare % SWC_COUNT, "RpSpare", spare * 11, PortDirection.R)

    access_points = []
    for index, port in enumerate(ports[:ACCESS_POINT_COUNT]):
        swc = port.swc_path.rsplit("/", 1)[-1]
        swc_index = int(swc[-3:])
        number = index % RUNNABLES_PER_SWC
        task = TASKS[(swc_index + number) % len(TASKS)]
        runnable_path = f"{port.swc_path}/{swc}_InternalBehavior/{swc}_Run{number}_{task[5:]}"
        if port.direction is PortDirection.P:
            kind = AccessKind.IMPLICIT_WRITE if index % 2 else AccessKind.EXPLICIT_SEND
            prefix = "DWA" if index % 2 else "DSP"
        else:
            kind = AccessKind.IMPLICIT_READ if index % 2 else AccessKind.EXPLICIT_READ
            prefix = "DRA" if index % 2 else "DRPA"
        target = port.com_specs[0].target
        access_points.append(
            AccessPoint(
                path=f"{runnable_path}/{prefix}_{port.name}_{target}",
                swc_path=port.swc_path,
                runnable_path=runnable_path,
                port_path=port.path,
                target=target,
                kind=kind,
                origin=port.origin,
            )
        )

    return Model(
        components=components,
        ports=ports,
        interfaces=interfaces,
        runnables=runnables,
        access_points=access_points,
        triggers=triggers,
        task_mappings=task_mappings,
        tasks=TASKS,
        compositions=[Composition(path=COMPOSITION, name="EcuComposition", origin=origin_c)],
        prototypes=prototypes,
        connectors=connectors,
        data_types=data_types,
        base_types=base_types,
        compu_methods=compu_methods,
        constraints=constraints,
        units=units,
        files=[
            SourceFile(rel_path=rel_path, size=10_000 + i, mtime_ns=1_700_000_000_000_000_000 + i)
            for i, rel_path in enumerate(files)
        ],
        baseline_commit="89abcdef0123456789abcdef0123456789abcdef",
    )
