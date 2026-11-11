"""The plan of a synthetic repo: every object, name and relation. No XML and no files.

``build_plan(spec)`` is deterministic: all choices come from ``random.Random(spec.seed)``.
The file layout does not influence the plan, so two specs that differ only in
``layout`` describe the same model.

Naming used in the plan (a convention of this generator, not of DaVinci):

* SWC types        ``/ComponentTypes/Swc<n>``; behaviour ``<Swc>_InternalBehavior``
* ports            ``Pp<Interface without "If">`` and ``Rp...``
* runnables        ``<Swc>_Init``, ``<Swc>_Cyclic10ms``, ``<Swc>_Cyclic100ms``,
  ``<Swc>_On<Port>`` (on data reception) and ``<Port>_<Operation>`` (server runnable)
* events           ``IT_``, ``TMT_``, ``DRT_``, ``OIT_`` plus the runnable name
* access points    ``DRA_`` implicit read, ``DWA_`` implicit write, ``DRPA_`` explicit read,
  ``DSP_`` explicit send, ``SSCP_`` synchronous server call, plus ``<Port>_<Target>``
* connectors       ``<provider>_<provider port>_<requester>_<requester port>``
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from porttool.core.models import AccessKind, ArxmlOwner, ComponentKind, TriggerKind
from tests.fixtures.generator.spec import GeneratorSpec

CT = "/ComponentTypes"
IF = "/PortInterfaces"
DT = "/DataTypes"
BASE_TYPES = f"{DT}/BaseTypes"
UNITS = f"{DT}/Units"
COMPU_METHODS = f"{DT}/CompuMethods"
DATA_CONSTRS = f"{DT}/DataConstrs"
COMPOSITION_NAME = "EcuComposition"
COMPOSITION = f"{CT}/{COMPOSITION_NAME}"
#: PENDING-DISCOVERY: the package of the ECUC module configurations in the real ECUC file.
ECUC_PACKAGE = "/ActiveEcuC"

TASK_INIT = "Task_Init"
TASK_10MS = "Task_10ms"
TASK_100MS = "Task_100ms"
TASK_EVENT = "Task_Event"
TASKS = (TASK_INIT, TASK_10MS, TASK_100MS, TASK_EVENT)

VALUE = "value"
ENUM = "enum"
ARRAY = "array"
RECORD = "record"
#: Category of the n-th implementation data type: one of each kind among the first four.
_CATEGORY_CYCLE = (VALUE, ENUM, ARRAY, RECORD, VALUE, VALUE, ENUM, VALUE, VALUE, VALUE)

_BASE_TYPES = (
    # name, size in bits, encoding, native declaration, lower limit, upper limit
    ("uint8", 8, "NONE", "unsigned char", 0.0, 255.0),
    ("uint16", 16, "NONE", "unsigned short", 0.0, 65535.0),
    ("uint32", 32, "NONE", "unsigned long", 0.0, 4294967295.0),
    ("sint8", 8, "2C", "signed char", -128.0, 127.0),
    ("sint16", 16, "2C", "signed short", -32768.0, 32767.0),
    ("sint32", 32, "2C", "signed long", -2147483648.0, 2147483647.0),
    ("float32", 32, "IEEE754", "float", -1000000.0, 1000000.0),
)
_UNITS = (
    ("NoUnit", "-"),
    ("KiloMeterPerHour", "km/h"),
    ("RevolutionPerMinute", "1/min"),
    ("NewtonMeter", "Nm"),
    ("DegreeCelsius", "°C"),
    ("Volt", "V"),
    ("Ampere", "A"),
    ("Percent", "%"),
)
_FACTORS = (1.0, 0.1, 0.5, 0.01)
_OFFSETS = (0.0, 0.0, -40.0)
_ENUM_NAMES = ("OFF", "ON", "ERROR", "INIT", "STANDBY", "ACTIVE")


@dataclass(slots=True)
class BaseTypePlan:
    name: str
    size_bits: int
    encoding: str
    native: str
    lower: float
    upper: float
    uuid: str

    @property
    def path(self) -> str:
        return f"{BASE_TYPES}/{self.name}"


@dataclass(slots=True)
class UnitPlan:
    name: str
    display: str
    uuid: str

    @property
    def path(self) -> str:
        return f"{UNITS}/{self.name}"


@dataclass(slots=True)
class DataTypePlan:
    name: str
    category: str  # VALUE, ENUM, ARRAY or RECORD
    uuid: str
    compu_uuid: str = ""
    constr_uuid: str = ""
    base_type: BaseTypePlan | None = None
    unit: UnitPlan | None = None
    factor: float = 1.0
    offset: float = 0.0
    lower: float = 0.0
    upper: float = 0.0
    enum_values: tuple[tuple[int, str], ...] = ()
    element: DataTypePlan | None = None  # array element type
    array_size: int = 0
    fields: tuple[tuple[str, DataTypePlan], ...] = ()  # record elements
    file: str = ""

    @property
    def path(self) -> str:
        return f"{DT}/{self.name}"

    @property
    def is_scalar(self) -> bool:
        return self.category in (VALUE, ENUM)

    @property
    def compu_path(self) -> str:
        return f"{COMPU_METHODS}/CM_{self.name}"

    @property
    def constr_path(self) -> str:
        return f"{DATA_CONSTRS}/DC_{self.name}"


@dataclass(slots=True)
class ElementPlan:
    name: str
    data_type: DataTypePlan
    uuid: str

    @property
    def init_value(self) -> str | None:
        """Init value text of the com specs: only scalar elements get one."""
        return "0" if self.data_type.is_scalar else None


@dataclass(slots=True)
class ArgumentPlan:
    name: str
    direction: str  # "IN" or "OUT"
    data_type: DataTypePlan


@dataclass(slots=True)
class OperationPlan:
    name: str
    arguments: tuple[ArgumentPlan, ...]
    possible_errors: tuple[str, ...]
    uuid: str


@dataclass(slots=True)
class InterfacePlan:
    name: str
    client_server: bool
    uuid: str
    elements: tuple[ElementPlan, ...] = ()
    operations: tuple[OperationPlan, ...] = ()
    errors: tuple[tuple[str, int], ...] = ()
    file: str = ""

    @property
    def path(self) -> str:
        return f"{IF}/{self.name}"

    @property
    def targets(self) -> tuple[str, ...]:
        """Names of the data elements or operations."""
        if self.client_server:
            return tuple(operation.name for operation in self.operations)
        return tuple(element.name for element in self.elements)


@dataclass(slots=True, eq=False)
class PortPlan:
    swc: SwcPlan
    provider: bool
    wants_connector: bool
    uuid: str
    interface: InterfacePlan | None = None  # set when the connections are planned
    name: str = ""
    fan_out: int = 0  # provider: number of connectors attached

    @property
    def path(self) -> str:
        return f"{self.swc.path}/{self.name}"


@dataclass(slots=True)
class AccessPlan:
    name: str
    kind: AccessKind
    port: PortPlan
    target: str
    uuid: str


@dataclass(slots=True)
class RunnablePlan:
    name: str
    symbol: str
    uuid: str
    accesses: list[AccessPlan] = field(default_factory=list)


@dataclass(slots=True)
class EventPlan:
    name: str
    kind: TriggerKind
    runnable: RunnablePlan
    uuid: str
    port: PortPlan | None = None
    target: str | None = None
    period_s: float | None = None
    task: str | None = None
    position: int | None = None


@dataclass(slots=True, eq=False)
class SwcPlan:
    name: str
    kind: ComponentKind
    uuid: str
    behavior_uuid: str
    prototype_uuid: str
    owner: ArxmlOwner = ArxmlOwner.DAVINCI
    team: str = ""
    authoring_tool: str | None = None
    export_folder: str | None = None
    port_slots: int = 0
    ports: list[PortPlan] = field(default_factory=list)
    runnables: list[RunnablePlan] = field(default_factory=list)
    events: list[EventPlan] = field(default_factory=list)
    file: str = ""

    @property
    def path(self) -> str:
        return f"{CT}/{self.name}"

    @property
    def behavior_path(self) -> str:
        return f"{self.path}/{self.name}_InternalBehavior"

    @property
    def prototype_path(self) -> str:
        return f"{COMPOSITION}/{self.name}"


@dataclass(slots=True)
class ConnectorPlan:
    name: str
    provider: PortPlan
    requester: PortPlan
    uuid: str

    @property
    def path(self) -> str:
        return f"{COMPOSITION}/{self.name}"


@dataclass(slots=True)
class Plan:
    spec: GeneratorSpec
    base_types: list[BaseTypePlan]
    units: list[UnitPlan]
    data_types: list[DataTypePlan]
    interfaces: list[InterfacePlan]
    swcs: list[SwcPlan]
    connectors: list[ConnectorPlan]
    composition_uuid: str
    tasks: tuple[str, ...] = TASKS


class _Ids:
    """Deterministic UUIDs from the plan's random generator."""

    def __init__(self, rng: random.Random) -> None:
        self._rng = rng

    def new(self) -> str:
        text = f"{self._rng.getrandbits(128):032x}"
        return f"{text[:8]}-{text[8:12]}-4{text[13:16]}-a{text[17:20]}-{text[20:]}"


def _numbered(prefix: str, index: int, count: int) -> str:
    return f"{prefix}{index + 1:0{len(str(count))}d}"


def _plan_data_types(
    count: int, base_types: list[BaseTypePlan], units: list[UnitPlan], ids: _Ids, rng: random.Random
) -> list[DataTypePlan]:
    uint8 = base_types[0]
    result: list[DataTypePlan] = []
    scalars: list[DataTypePlan] = []
    values: list[DataTypePlan] = []
    for index in range(count):
        category = _CATEGORY_CYCLE[index % len(_CATEGORY_CYCLE)]
        if category in (ARRAY, RECORD) and not values:
            category = VALUE
        number = f"{index + 1:0{max(3, len(str(count)))}d}"
        if category == VALUE:
            base = base_types[len(values) % len(base_types)]
            data_type = DataTypePlan(
                name=f"T_Val{number}",
                category=VALUE,
                uuid=ids.new(),
                compu_uuid=ids.new(),
                constr_uuid=ids.new(),
                base_type=base,
                unit=units[len(values) % len(units)],
                factor=_FACTORS[len(values) % len(_FACTORS)],
                offset=_OFFSETS[len(values) % len(_OFFSETS)],
                lower=base.lower,
                upper=base.upper,
            )
            values.append(data_type)
            scalars.append(data_type)
        elif category == ENUM:
            size = 2 + len(scalars) % (len(_ENUM_NAMES) - 1)
            data_type = DataTypePlan(
                name=f"T_Enum{number}",
                category=ENUM,
                uuid=ids.new(),
                compu_uuid=ids.new(),
                constr_uuid=ids.new(),
                base_type=uint8,
                enum_values=tuple(enumerate(_ENUM_NAMES[:size])),
                lower=0.0,
                upper=float(size - 1),
            )
            scalars.append(data_type)
        elif category == ARRAY:
            data_type = DataTypePlan(
                name=f"T_Arr{number}",
                category=ARRAY,
                uuid=ids.new(),
                element=rng.choice(values),
                array_size=rng.randint(2, 16),
            )
        else:
            data_type = DataTypePlan(
                name=f"T_Rec{number}",
                category=RECORD,
                uuid=ids.new(),
                fields=tuple(
                    (f"Field{position + 1}", rng.choice(scalars))
                    for position in range(rng.randint(2, 3))
                ),
            )
        result.append(data_type)
    return result


def _plan_interfaces(
    count: int, share: float, data_types: list[DataTypePlan], ids: _Ids, rng: random.Random
) -> list[InterfacePlan]:
    scalars = [data_type for data_type in data_types if data_type.is_scalar]
    client_server = max(1, round(count * share)) if count >= 2 and share > 0 else 0
    # Spread the client-server interfaces evenly over the list.
    step = count / client_server if client_server else 0.0
    cs_indexes = {int(step * position + step / 2) for position in range(client_server)}
    result: list[InterfacePlan] = []
    for index in range(count):
        if index in cs_indexes:
            operations: list[OperationPlan] = []
            with_error = rng.random() < 0.5
            for position in range(rng.randint(1, 2)):
                arguments = [ArgumentPlan("ArgIn", "IN", rng.choice(scalars))]
                if rng.random() < 0.7:
                    arguments.append(ArgumentPlan("ArgOut", "OUT", rng.choice(data_types)))
                operations.append(
                    OperationPlan(
                        name=f"Op{position + 1}",
                        arguments=tuple(arguments),
                        possible_errors=("E_NOT_OK",) if with_error else (),
                        uuid=ids.new(),
                    )
                )
            result.append(
                InterfacePlan(
                    name=_numbered("IfSrv", index, count),
                    client_server=True,
                    uuid=ids.new(),
                    operations=tuple(operations),
                    errors=(("E_NOT_OK", 1),) if with_error else (),
                )
            )
        else:
            size = rng.choices((1, 2, 3), weights=(70, 20, 10))[0]
            result.append(
                InterfacePlan(
                    name=_numbered("IfSig", index, count),
                    client_server=False,
                    uuid=ids.new(),
                    elements=tuple(
                        ElementPlan(f"De{position + 1}", rng.choice(data_types), ids.new())
                        for position in range(size)
                    ),
                )
            )
    return result


def _plan_swcs(spec: GeneratorSpec, ids: _Ids, rng: random.Random) -> list[SwcPlan]:
    count = spec.swc_count
    base, extra = divmod(spec.total_ports, count)
    teams = max(1, count // 10)
    swcs: list[SwcPlan] = []
    for index in range(count):
        kind = ComponentKind.APPLICATION
        if count >= 20 and index % 20 == 9:
            kind = ComponentKind.SENSOR_ACTUATOR
        elif count >= 20 and index % 20 == 19:
            kind = ComponentKind.COMPLEX_DEVICE_DRIVER
        swcs.append(
            SwcPlan(
                name=_numbered("Swc", index, count),
                kind=kind,
                uuid=ids.new(),
                behavior_uuid=ids.new(),
                prototype_uuid=ids.new(),
                team=_numbered("Team", index % teams, teams),
                port_slots=base + (1 if index < extra else 0),
            )
        )
    special = rng.sample(range(count), spec.model_owned_count + spec.locked_count)
    for position, index in enumerate(sorted(special[: spec.model_owned_count])):
        swc = swcs[index]
        swc.owner = ArxmlOwner.MODEL
        swc.authoring_tool = "Simulink" if position % 2 == 0 else "TargetLink"
        swc.export_folder = f"Models/{swc.name}/arxml"
    for index in special[spec.model_owned_count :]:
        swcs[index].owner = ArxmlOwner.LOCKED
    return swcs


def _role_counts(spec: GeneratorSpec) -> tuple[int, int, int, int]:
    """Ports per role: connected providers, other providers, connected requesters, the rest."""
    total = spec.total_ports
    connectors = spec.connector_count
    spare = total - connectors  # ports that are providers or unconnected requesters
    if connectors == 0:
        providers = spare // 2
        return 0, providers, 0, spare - providers
    providers = min(spare, max(1, round(spare * 0.8)))
    unconnected_providers = providers // 10 if providers > 1 else 0
    connected_providers = min(providers - unconnected_providers, connectors)
    unconnected_providers = providers - connected_providers
    return connected_providers, unconnected_providers, connectors, spare - providers


def _plan_ports(spec: GeneratorSpec, swcs: list[SwcPlan], ids: _Ids, rng: random.Random) -> None:
    """Give every SWC its ports with a role; interfaces and names follow later."""
    connected_p, unconnected_p, connected_r, unconnected_r = _role_counts(spec)
    roles = (
        [(True, True)] * connected_p
        + [(True, False)] * unconnected_p
        + [(False, True)] * connected_r
        + [(False, False)] * unconnected_r
    )
    rng.shuffle(roles)
    # Deal the roles round-robin so that neighbours in the list sit on different SWCs.
    owners: list[SwcPlan] = []
    for row in range(max(swc.port_slots for swc in swcs)):
        owners.extend(swc for swc in swcs if row < swc.port_slots)
    for swc, (provider, wants_connector) in zip(owners, roles, strict=True):
        swc.ports.append(
            PortPlan(swc=swc, provider=provider, wants_connector=wants_connector, uuid=ids.new())
        )


def _plan_connections(
    swcs: list[SwcPlan], interfaces: list[InterfacePlan], ids: _Ids, rng: random.Random
) -> list[tuple[PortPlan, PortPlan]]:
    """Choose each port's interface and pair every connected requester with a provider.

    A requester gets exactly one connector (one sender per receiver, one server per
    client); a provider serves any number of requesters on other SWCs.
    """
    ports = [port for swc in swcs for port in swc.ports]
    providers = [port for port in ports if port.provider]
    order = list(interfaces)
    rng.shuffle(order)
    for index, port in enumerate(providers):
        port.interface = order[index % len(order)]
    targets = [port for port in providers if port.wants_connector]
    pairs: list[tuple[PortPlan, PortPlan]] = []
    pointer = 0
    for port in ports:
        if port.provider:
            continue
        if not port.wants_connector:
            port.interface = rng.choice(interfaces)
            continue
        for step in range(len(targets)):
            candidate = targets[(pointer + step) % len(targets)]
            if candidate.swc is not port.swc:
                break
        else:
            raise ValueError(
                f"cannot connect a requester port of {port.swc.name}: every provider port "
                "that takes connectors sits on the same SWC. Use more SWCs or fewer connectors."
            )
        if step == 0:
            pointer = (pointer + 1) % len(targets)
        port.interface = candidate.interface
        candidate.fan_out += 1
        pairs.append((candidate, port))
    return pairs


def _name_ports(swcs: list[SwcPlan]) -> None:
    for swc in swcs:
        used: dict[str, int] = {}
        for port in swc.ports:
            assert port.interface is not None
            base = ("Pp" if port.provider else "Rp") + port.interface.name.removeprefix("If")
            used[base] = used.get(base, 0) + 1
            port.name = base if used[base] == 1 else f"{base}_{used[base]}"


def _plan_behavior(swc: SwcPlan, ids: _Ids, rng: random.Random) -> None:
    def runnable(name: str, symbol: str | None = None) -> RunnablePlan:
        planned = RunnablePlan(name=name, symbol=symbol or name, uuid=ids.new())
        swc.runnables.append(planned)
        return planned

    init = runnable(f"{swc.name}_Init")
    fast = runnable(f"{swc.name}_Cyclic10ms")
    slow = runnable(f"{swc.name}_Cyclic100ms")
    swc.events.append(EventPlan(f"IT_{init.name}", TriggerKind.INIT, init, ids.new()))
    swc.events.append(
        EventPlan(f"TMT_{fast.name}", TriggerKind.PERIODIC, fast, ids.new(), period_s=0.01)
    )
    swc.events.append(
        EventPlan(f"TMT_{slow.name}", TriggerKind.PERIODIC, slow, ids.new(), period_s=0.1)
    )
    on_data = 0
    for port in swc.ports:
        interface = port.interface
        assert interface is not None
        cyclic = fast if rng.random() < 0.7 else slow
        if interface.client_server and port.provider:
            for operation in interface.operations:
                server = runnable(
                    f"{port.name}_{operation.name}", f"{swc.name}_{port.name}_{operation.name}"
                )
                swc.events.append(
                    EventPlan(
                        f"OIT_{server.name}",
                        TriggerKind.OPERATION_INVOKED,
                        server,
                        ids.new(),
                        port=port,
                        target=operation.name,
                    )
                )
            continue
        if interface.client_server:
            prefix, kind, owner = "SSCP", AccessKind.SYNC_SERVER_CALL, cyclic
        elif port.provider:
            if rng.random() < 0.6:
                prefix, kind, owner = "DWA", AccessKind.IMPLICIT_WRITE, cyclic
            else:
                prefix, kind, owner = "DSP", AccessKind.EXPLICIT_SEND, cyclic
        elif port.wants_connector and on_data < 2 and rng.random() < 0.1:
            on_data += 1
            owner = runnable(f"{swc.name}_On{port.name}")
            prefix, kind = "DRPA", AccessKind.EXPLICIT_READ
            swc.events.append(
                EventPlan(
                    f"DRT_{owner.name}",
                    TriggerKind.DATA_RECEIVED,
                    owner,
                    ids.new(),
                    port=port,
                    target=interface.targets[0],
                )
            )
        elif rng.random() < 0.5:
            prefix, kind, owner = "DRA", AccessKind.IMPLICIT_READ, cyclic
        else:
            prefix, kind, owner = "DRPA", AccessKind.EXPLICIT_READ, cyclic
        for target in interface.targets:
            owner.accesses.append(
                AccessPlan(f"{prefix}_{port.name}_{target}", kind, port, target, ids.new())
            )


def _map_events_to_tasks(swcs: list[SwcPlan]) -> None:
    """Every event gets a task and a position, except operation-invoked events."""
    positions = dict.fromkeys(TASKS, 0)
    for swc in swcs:
        for event in swc.events:
            if event.kind is TriggerKind.INIT:
                task = TASK_INIT
            elif event.kind is TriggerKind.PERIODIC:
                task = TASK_10MS if event.period_s == 0.01 else TASK_100MS
            elif event.kind is TriggerKind.DATA_RECEIVED:
                task = TASK_EVENT
            else:
                continue  # server runnables run in the caller's context: no task mapping
            positions[task] += 1
            event.task = task
            event.position = positions[task]


def build_plan(spec: GeneratorSpec) -> Plan:
    """Return the complete, deterministic plan for ``spec``. Raises ``ValueError`` if impossible."""
    spec.validate()
    rng = random.Random(spec.seed)
    ids = _Ids(rng)
    base_types = [BaseTypePlan(*row, uuid=ids.new()) for row in _BASE_TYPES]
    units = [UnitPlan(name, display, ids.new()) for name, display in _UNITS]
    data_type_count = spec.data_type_count or max(4, spec.total_ports // 20)
    data_types = _plan_data_types(data_type_count, base_types, units, ids, rng)
    swcs = _plan_swcs(spec, ids, rng)
    _plan_ports(spec, swcs, ids, rng)
    provider_count = sum(1 for swc in swcs for port in swc.ports if port.provider)
    interface_count = spec.interface_count or max(2, provider_count // 2)
    interfaces = _plan_interfaces(interface_count, spec.client_server_share, data_types, ids, rng)
    pairs = _plan_connections(swcs, interfaces, ids, rng)
    _name_ports(swcs)
    for swc in swcs:
        _plan_behavior(swc, ids, rng)
    _map_events_to_tasks(swcs)
    connectors = [
        ConnectorPlan(
            name=f"{provider.swc.name}_{provider.name}_{requester.swc.name}_{requester.name}",
            provider=provider,
            requester=requester,
            uuid=ids.new(),
        )
        for provider, requester in pairs
    ]
    return Plan(
        spec=spec,
        base_types=base_types,
        units=units,
        data_types=data_types,
        interfaces=interfaces,
        swcs=swcs,
        connectors=connectors,
        composition_uuid=ids.new(),
    )
