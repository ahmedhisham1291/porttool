"""verify_model: pure comparison of the model before the apply with the reloaded one."""

from __future__ import annotations

import pytest

from porttool.core.models import (
    AccessKind,
    DataTypeCategory,
    DataTypeLevel,
    InterfaceKind,
    Model,
    PortDirection,
    TriggerKind,
)
from porttool.core.operations import (
    AddAccessPoint,
    AddConnector,
    AddDataType,
    AddInterface,
    AddPort,
    AddServerRunnable,
    AddTrigger,
    AnyOperation,
    DeleteAccessPoint,
    DeleteConnector,
    DeletePort,
    DeleteTrigger,
    ElementSpec,
    ModifyPort,
)
from porttool.pipeline import codes
from porttool.pipeline.describe import object_text, operation_text, owner_path, source_text
from porttool.pipeline.verify import verify_model
from tests.helpers.sample_model import COMPOSITION, CT, DT, IF, build_sample_model

A, B = f"{CT}/SwcA", f"{CT}/SwcB"


@pytest.fixture(scope="module")
def model() -> Model:
    return build_sample_model()


def _connector(name: str, requester_port: str = "RpSpeed") -> AddConnector:
    return AddConnector(
        id="c",
        composition_path=COMPOSITION,
        name=name,
        provider_prototype="SwcA",
        provider_port="PpSpeed",
        requester_prototype="SwcB",
        requester_port=requester_port,
    )


def _data_type(name: str, level: DataTypeLevel) -> AddDataType:
    return AddDataType(
        id="d", package_path=DT, name=name, level=level, category=DataTypeCategory.VALUE
    )


def _interface(name: str, kind: InterfaceKind, element: str) -> AddInterface:
    return AddInterface(
        id="i",
        package_path=IF,
        name=name,
        kind=kind,
        elements=(ElementSpec(name=element, type_path=f"{DT}/uint16"),),
    )


def _port(name: str, direction: PortDirection, interface: str) -> AddPort:
    return AddPort(
        id="p", swc_path=A, name=name, direction=direction, interface_path=f"{IF}/{interface}"
    )


def _access(runnable: str, target: str, kind: AccessKind) -> AddAccessPoint:
    return AddAccessPoint(
        id="a", swc_path=A, runnable_name=runnable, port_name="PpSpeed", target=target, kind=kind
    )


def _trigger(
    kind: TriggerKind,
    period_s: float | None = None,
    runnable: str = "RunA_10ms",
    port_name: str | None = None,
    target: str | None = None,
) -> AddTrigger:
    return AddTrigger(
        id="t",
        swc_path=B if port_name else A,
        runnable_name=runnable,
        kind=kind,
        period_s=period_s,
        port_name=port_name,
        target=target,
    )


IN_MODEL: list[AnyOperation] = [
    _data_type("uint16", DataTypeLevel.IMPLEMENTATION),
    _interface("IfSpeed", InterfaceKind.SENDER_RECEIVER, "Speed"),
    _port("PpSpeed", PortDirection.P, "IfSpeed"),
    ModifyPort(id="m", swc_path=A, name="PpSpeed", interface_path=f"{IF}/IfSpeed"),
    AddServerRunnable(
        id="s",
        swc_path=B,
        name="PpDiag_ReadDtc",
        symbol="x",
        port_name="PpDiag",
        operation="ReadDtc",
    ),
    _access("RunA_10ms", "Speed", AccessKind.IMPLICIT_WRITE),
    _trigger(TriggerKind.PERIODIC, 0.01),
    _trigger(
        TriggerKind.DATA_RECEIVED, runnable="RunB_OnSpeed", port_name="RpSpeed", target="Speed"
    ),
    _connector("SwcA_PpSpeed_SwcB_RpSpeed"),
    DeletePort(id="x", swc_path=A, name="PpGone"),
    DeleteConnector(id="x", composition_path=COMPOSITION, name="Gone"),
    DeleteTrigger(id="x", swc_path=A, name="TMT_Gone"),
    DeleteAccessPoint(
        id="x",
        swc_path=A,
        runnable_name="RunA_10ms",
        port_name="PpSpeed",
        target="Speed",
        kind=AccessKind.EXPLICIT_SEND,
    ),  # fmt: skip
]

NOT_AS_REQUESTED: list[tuple[AnyOperation, str]] = [
    (_data_type("Missing", DataTypeLevel.IMPLEMENTATION), codes.VER_MISSING),
    (_data_type("uint16", DataTypeLevel.APPLICATION), codes.VER_MISMATCH),
    (_interface("IfNew", InterfaceKind.SENDER_RECEIVER, "Speed"), codes.VER_MISSING),
    (_interface("IfSpeed", InterfaceKind.CLIENT_SERVER, "Speed"), codes.VER_MISMATCH),
    (_interface("IfSpeed", InterfaceKind.SENDER_RECEIVER, "Other"), codes.VER_MISMATCH),
    (_port("PpNew", PortDirection.P, "IfSpeed"), codes.VER_MISSING),
    (_port("PpSpeed", PortDirection.R, "IfSpeed"), codes.VER_MISMATCH),
    (_port("PpSpeed", PortDirection.P, "IfTorque"), codes.VER_MISMATCH),
    (
        ModifyPort(id="m", swc_path=A, name="PpSpeed", interface_path=f"{IF}/IfTorque"),
        codes.VER_MISMATCH,
    ),  # fmt: skip
    (
        AddServerRunnable(
            id="s", swc_path=B, name="Nope", symbol="x", port_name="PpDiag", operation="ReadDtc"
        ),
        codes.VER_MISSING,
    ),  # fmt: skip
    (_access("RunA_10ms", "Speed", AccessKind.EXPLICIT_SEND), codes.VER_MISSING),
    (_access("NoRunnable", "Speed", AccessKind.IMPLICIT_WRITE), codes.VER_MISSING),
    (_trigger(TriggerKind.PERIODIC, 0.02), codes.VER_MISSING),
    (_trigger(TriggerKind.INIT), codes.VER_MISSING),
    (
        _trigger(
            TriggerKind.DATA_RECEIVED, runnable="RunB_OnSpeed", port_name="RpSpeed", target="Other"
        ),
        codes.VER_MISSING,
    ),  # fmt: skip
    (_connector("NewConnector"), codes.VER_MISSING),
    (_connector("SwcA_PpSpeed_SwcB_RpSpeed", requester_port="PpDiag"), codes.VER_MISMATCH),
    (_connector("SwcA_PpSpeed_SwcB_RpSpeed", requester_port="NoPort"), codes.VER_MISMATCH),
    (DeletePort(id="x", swc_path=A, name="PpSpeed"), codes.VER_STILL_THERE),
    (
        DeleteConnector(id="x", composition_path=COMPOSITION, name="SwcA_PpSpeed_SwcB_RpSpeed"),
        codes.VER_STILL_THERE,
    ),  # fmt: skip
    (DeleteTrigger(id="x", swc_path=A, name="TMT_RunA_10ms"), codes.VER_STILL_THERE),
    (
        DeleteAccessPoint(
            id="x",
            swc_path=A,
            runnable_name="RunA_10ms",
            port_name="PpSpeed",
            target="Speed",
            kind=AccessKind.IMPLICIT_WRITE,
        ),
        codes.VER_STILL_THERE,
    ),  # fmt: skip
]


def test_operations_that_are_in_the_model_verify_cleanly(model: Model) -> None:
    assert verify_model(model, model, IN_MODEL) == ()


@pytest.mark.parametrize(
    ("operation", "code"),
    NOT_AS_REQUESTED,
    ids=[f"{o.op}-{c}-{n}" for n, (o, c) in enumerate(NOT_AS_REQUESTED)],
)
def test_each_operation_kind_is_checked_against_the_reloaded_model(
    model: Model, operation: AnyOperation, code: str
) -> None:
    (finding,) = verify_model(model, model, [operation])
    assert finding.code == code and finding.object_path == operation.id
    assert operation_text(operation) in finding.message


def test_every_operation_kind_has_a_text(model: Model) -> None:
    for operation in [*IN_MODEL, *(op for op, _code in NOT_AS_REQUESTED)]:
        assert owner_path(operation).startswith("/")
        assert object_text(operation) and operation.op.replace("_", " ") in operation_text(
            operation
        )
    with_source = _port("PpX", PortDirection.P, "IfSpeed").model_copy(
        update={"source_file": r"C:\in\requests.xlsx", "source_sheet": "Ports", "source_row": 7}
    )
    assert source_text(with_source) == "requests.xlsx Ports row 7"
    assert source_text(IN_MODEL[0]) == ""
