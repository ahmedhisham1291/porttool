"""Smoke tests for the v0 contracts in porttool.core."""

from __future__ import annotations

from pathlib import Path

import pytest

from porttool.core.errors import ConfigError, LicenseError, PortToolError, ToolRunError
from porttool.core.models import ArxmlOwner, PortDirection, TriggerKind
from porttool.core.operations import (
    APPLY_ORDER,
    AddConnector,
    AddPort,
    AddTrigger,
    ChangeSet,
    InitValueSpec,
    Route,
)
from porttool.core.pending import PendingEntry, PendingQueue, PendingState
from porttool.core.sheets import COLUMNS, REQUEST_SHEETS, Sheet
from tests.helpers.sample_model import COMPOSITION, CT, IF, build_sample_config, build_sample_model


def test_sample_model_indexes() -> None:
    model = build_sample_model()
    assert {c.name for c in model.components.values()} == {"SwcA", "SwcB", "SwcM", "EcuComposition"}
    assert [p.name for p in model.ports_of(f"{CT}/SwcA")] == ["PpSpeed", "RpTorque", "RpDiag"]
    port = model.find_port(f"{CT}/SwcB", "RpSpeed")
    assert port is not None and port.direction is PortDirection.R
    assert model.find_port(f"{CT}/SwcB", "Nope") is None
    assert len(model.ports_using(f"{IF}/IfSpeed")) == 2
    assert model.components_named("SwcM")[0].owner is ArxmlOwner.MODEL


def test_unconnected_ports() -> None:
    model = build_sample_model()
    unconnected = {(i.prototype.name, i.port.name) for i in model.unconnected()}
    assert unconnected == {("SwcA", "RpTorque"), ("SwcM", "PpTorque")}
    assert len(model.connectors_in(COMPOSITION)) == 2
    at_speed = model.connectors_at(f"{COMPOSITION}/SwcA", f"{CT}/SwcA/PpSpeed")
    assert at_speed[0].name.startswith("SwcA_")


def test_behavior_lookups() -> None:
    model = build_sample_model()
    runnable = model.find_runnable(f"{CT}/SwcA", "RunA_10ms")
    assert runnable is not None
    assert len(model.access_points_of_runnable(runnable.path)) == 2
    trigger = model.triggers_of(runnable.path)[0]
    mapping = model.task_of(trigger.path)
    assert mapping is not None and mapping.task == "Task_10ms"
    server = model.find_runnable(f"{CT}/SwcB", "PpDiag_ReadDtc")
    assert server is not None
    assert model.task_of(model.triggers_of(server.path)[0].path) is None


def test_model_hash_is_stable_and_file_sensitive() -> None:
    assert build_sample_model().model_hash == build_sample_model().model_hash
    assert len(build_sample_model().model_hash) == 64


def test_change_set_json_round_trip() -> None:
    change_set = ChangeSet(
        baseline_commit="abc",
        created_utc="2026-11-11T00:00:00Z",
        operations=(
            AddPort(
                id="op1",
                swc_path=f"{CT}/SwcA",
                name="RpNew",
                direction=PortDirection.R,
                interface_path=f"{IF}/IfSpeed",
                init_values=(InitValueSpec(element="Speed", value="0"),),
            ),
            AddConnector(
                id="op2",
                route=Route.HOLD,
                composition_path=COMPOSITION,
                name="SwcM_PpTorque_SwcA_RpTorque",
                provider_prototype="SwcM",
                provider_port="PpTorque",
                requester_prototype="SwcA",
                requester_port="RpTorque",
            ),
        ),
    )
    restored = ChangeSet.from_json(change_set.to_json())
    assert restored == change_set
    assert isinstance(restored.operations[0], AddPort)
    assert [op.id for op in restored.routed(Route.HOLD)] == ["op2"]


def test_every_operation_kind_has_an_apply_position() -> None:
    trigger = AddTrigger(
        id="t", swc_path="/a", runnable_name="r", kind=TriggerKind.PERIODIC, period_s=0.01
    )
    change_set = ChangeSet(operations=(trigger,))
    assert change_set.operations[0].op in APPLY_ORDER
    assert len(set(APPLY_ORDER)) == len(APPLY_ORDER)


def test_pending_queue_round_trip() -> None:
    entry = PendingEntry(
        operation=AddPort(
            id="op1",
            route=Route.MODEL,
            swc_path=f"{CT}/SwcM",
            name="PpNew",
            direction=PortDirection.P,
            interface_path=f"{IF}/IfSpeed",
        ),
        state=PendingState.WAITING_FOR_MODEL_OWNER,
        created_utc="2026-11-11T00:00:00Z",
        owner_team="Gamma",
    )
    queue = PendingQueue(entries=(entry,))
    assert PendingQueue.from_json(queue.to_json()) == queue


def test_sheet_layout_is_consistent() -> None:
    for sheet in REQUEST_SHEETS:
        keys = [column.key for column in COLUMNS[sheet]]
        assert len(keys) == len(set(keys)), sheet
        assert keys[0] == "row_id", sheet
        assert keys[-3:] == ["action", "requester", "comment"], sheet
    assert Sheet.RESULTS in COLUMNS


def test_errors_carry_code_and_message() -> None:
    error = ConfigError("porttool.toml not found", detail="C:/repo")
    assert str(error) == "[PT-E100] porttool.toml not found"
    assert isinstance(LicenseError(), ToolRunError)
    assert LicenseError().user_message
    with pytest.raises(PortToolError):
        raise error


def test_sample_config_owner_lookup(tmp_path: Path) -> None:
    config = build_sample_config(tmp_path)
    assert config.owner_of("SwcM") is ArxmlOwner.MODEL
    assert config.owner_of("Unknown") is ArxmlOwner.DAVINCI
    assert config.team_of("SwcA") == "Alpha"


def test_row_id_is_stable_and_prefixed() -> None:
    from porttool.core.rowid import interface_element_key, row_id

    first = row_id(Sheet.PORTS, f"{CT}/SwcA/PpSpeed")
    assert first == row_id(Sheet.PORTS, f"{CT}/SwcA/PpSpeed")
    assert first.startswith("PT-") and len(first) == 15
    assert first != row_id(Sheet.PORTS, f"{CT}/SwcA/RpTorque")
    assert row_id(Sheet.INTERFACE_ELEMENTS, interface_element_key(f"{IF}/IfDiag", "ReadDtc", "Dtc"))
