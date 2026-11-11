"""Timing against a large synthetic model: about 300 SWCs, 20,000 ports, 15,000 connectors."""

from __future__ import annotations

import time
from functools import cache
from pathlib import Path

from porttool.core.config import ProjectConfig
from porttool.core.models import (
    AccessKind,
    AccessPoint,
    BaseType,
    Component,
    ComponentKind,
    ComponentPrototype,
    Composition,
    ComSpec,
    Connector,
    ConnectorEnd,
    ConnectorKind,
    DataElement,
    DataType,
    DataTypeCategory,
    DataTypeLevel,
    InterfaceKind,
    Model,
    Port,
    PortDirection,
    PortInterface,
    Runnable,
    SourceFile,
    TaskMapping,
    Trigger,
    TriggerKind,
)
from porttool.core.pending import PendingQueue
from porttool.core.results import RawRequestRow, RequestStatus
from porttool.validator import validate
from tests.unit.validator.support import (
    NOW,
    access_row,
    check_invariants,
    connection_row,
    connector_rid,
    element_row,
    interface_row,
    number,
    port_row,
    trigger_row,
    type_row,
)

CT = "/ComponentTypes"
IF = "/PortInterfaces"
DT = "/DataTypes"
TOP = f"{CT}/Top"
SWCS = 300
PAIRS = 50  # connected P/R pairs per SWC -> 15,000 connectors
SPARE = 17  # unconnected ports per SWC -> 20,100 ports in total
INTERFACES = 500


def _interface(index: int) -> str:
    return f"{IF}/If{index % INTERFACES}"


@cache
def large_model() -> Model:
    u16 = f"{DT}/uint16"
    interfaces = [
        PortInterface(
            path=f"{IF}/If{i}",
            name=f"If{i}",
            kind=InterfaceKind.SENDER_RECEIVER,
            elements=(DataElement(name="Value", type_path=u16),),
        )
        for i in range(INTERFACES)
    ]
    components = [Component(path=TOP, name="Top", kind=ComponentKind.COMPOSITION)]
    ports: list[Port] = []
    prototypes: list[ComponentPrototype] = []
    connectors: list[Connector] = []
    runnables: list[Runnable] = []
    triggers: list[Trigger] = []
    mappings: list[TaskMapping] = []
    access: list[AccessPoint] = []
    for s in range(SWCS):
        swc = f"{CT}/Swc{s}"
        beh = f"{swc}/Swc{s}_InternalBehavior"
        components.append(
            Component(path=swc, name=f"Swc{s}", kind=ComponentKind.APPLICATION, behavior_path=beh)
        )
        prototypes.append(
            ComponentPrototype(
                path=f"{TOP}/Swc{s}", name=f"Swc{s}", composition_path=TOP, type_path=swc
            )
        )
        runnables.append(Runnable(path=f"{beh}/Run{s}", name=f"Run{s}", swc_path=swc))
        triggers.append(
            Trigger(
                path=f"{beh}/TMT_Run{s}",
                name=f"TMT_Run{s}",
                swc_path=swc,
                runnable_path=f"{beh}/Run{s}",
                kind=TriggerKind.PERIODIC,
                period_s=0.01,
            )
        )
        mappings.append(TaskMapping(event_path=f"{beh}/TMT_Run{s}", task="Task_10ms", position=s))
        spec = (ComSpec(target="Value", init_value="0"),)
        peer = (s + 1) % SWCS
        for p in range(PAIRS):
            interface = _interface(s * PAIRS + p)
            ports.append(Port(f"{swc}/Pp{p}", f"Pp{p}", swc, PortDirection.P, interface, spec))
            # The R port of the next SWC takes the same interface as this P port.
            ports.append(
                Port(
                    f"{CT}/Swc{peer}/Rp{p}",
                    f"Rp{p}",
                    f"{CT}/Swc{peer}",
                    PortDirection.R,
                    interface,
                    spec,
                )
            )
            name = f"Swc{s}_Pp{p}_Swc{peer}_Rp{p}"
            connectors.append(
                Connector(
                    path=f"{TOP}/{name}",
                    name=name,
                    composition_path=TOP,
                    kind=ConnectorKind.ASSEMBLY,
                    provider=ConnectorEnd(f"{TOP}/Swc{s}", f"{swc}/Pp{p}"),
                    requester=ConnectorEnd(f"{TOP}/Swc{peer}", f"{CT}/Swc{peer}/Rp{p}"),
                )
            )
            if p < 5:
                access.append(
                    AccessPoint(
                        path=f"{beh}/Run{s}/DWA_Pp{p}",
                        swc_path=swc,
                        runnable_path=f"{beh}/Run{s}",
                        port_path=f"{swc}/Pp{p}",
                        target="Value",
                        kind=AccessKind.IMPLICIT_WRITE,
                    )
                )
        for q in range(SPARE):
            direction = PortDirection.P if q % 2 else PortDirection.R
            prefix = "PpFree" if q % 2 else "RpFree"
            ports.append(
                Port(f"{swc}/{prefix}{q}", f"{prefix}{q}", swc, direction, _interface(q), spec)
            )
    return Model(
        components=components,
        ports=ports,
        interfaces=interfaces,
        runnables=runnables,
        access_points=access,
        triggers=triggers,
        task_mappings=mappings,
        tasks=("Task_10ms", "Task_Event"),
        compositions=[Composition(path=TOP, name="Top")],
        prototypes=prototypes,
        connectors=connectors,
        data_types=[
            DataType(
                path=u16,
                name="uint16",
                level=DataTypeLevel.IMPLEMENTATION,
                category=DataTypeCategory.VALUE,
                base_type_path=f"{DT}/BaseTypes/uint16",
            )
        ],
        base_types=[BaseType(f"{DT}/BaseTypes/uint16", "uint16", 16, "NONE")],
        files=[SourceFile(rel_path="big.arxml", size=1, mtime_ns=1)],
        baseline_commit="f" * 40,
    )


def requests(count: int) -> tuple[RawRequestRow, ...]:
    """A mixed import: 10 kinds of request, ``count`` rows in total, all of them valid."""
    rows: list[RawRequestRow] = []
    for i in range(count // 10):
        s = i % SWCS
        other = (s + 7) % SWCS
        rows.append(type_row(f"NewType{i}", base_type="uint16", upper_limit="1000"))
        rows.append(interface_row(f"IfNew{i}"))
        rows.append(element_row(f"IfNew{i}", "Value", f"NewType{i}"))
        rows.append(port_row(f"Swc{s}", f"PpNew{i}", "P", f"IfNew{i}", "5"))
        rows.append(port_row(f"Swc{other}", f"RpNew{i}", "R", f"IfNew{i}", "5"))
        rows.append(connection_row(f"Swc{s}", f"PpNew{i}", f"Swc{other}", f"RpNew{i}"))
        rows.append(access_row(f"Swc{s}", f"Run{s}", f"PpNew{i}", "Value", "implicit write"))
        rows.append(
            trigger_row(f"Swc{other}", f"Run{other}", "on data reception", f"RpNew{i}", "Value")
        )
        pair = i // SWCS + 10
        peer = (s + 1) % SWCS
        name = f"Swc{s}_Pp{pair}_Swc{peer}_Rp{pair}"
        rows.append(
            connection_row(
                f"Swc{s}",
                f"Pp{pair}",
                f"Swc{peer}",
                f"Rp{pair}",
                name=name,
                action="DELETE",
                rid=connector_rid(name, TOP),
            )
        )
        rows.append(port_row(f"Swc{s}", f"RpMore{i}", "R", f"If{i % INTERFACES}", "1"))
    return number(rows)


def _timed(count: int) -> float:
    model = large_model()
    config = ProjectConfig(repo_root=Path("C:/repo"))
    rows = requests(count)
    started = time.perf_counter()
    outcome = validate(model, config, rows, PendingQueue(), NOW)
    elapsed = time.perf_counter() - started
    assert all(result.status is RequestStatus.ACCEPTED for result in outcome.results)
    assert len(outcome.change_set.operations) == count - count // 10  # element rows share an op
    check_invariants(outcome, rows, model, PendingQueue(), NOW)
    return elapsed


def test_large_model_has_the_required_size() -> None:
    model = large_model()
    assert len(model.components) == SWCS + 1
    assert len(model.ports) >= 20_000
    assert len(model.connectors) == 15_000


def test_500_requests_against_the_large_model_within_two_seconds() -> None:
    elapsed = _timed(500)
    print(f"\n500 requests: {elapsed * 1000:.0f} ms")
    assert elapsed < 2.0, f"500 requests took {elapsed:.2f} s; the budget is 2 s"


def test_10000_requests_against_the_large_model() -> None:
    elapsed = _timed(10_000)
    print(f"\n10,000 requests: {elapsed:.2f} s")
    assert elapsed < 20.0, f"10,000 requests took {elapsed:.1f} s"
