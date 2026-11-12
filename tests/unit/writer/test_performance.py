"""Budget: 200 operations touching about 100 files of the LARGE repo within 5 s."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from porttool.core.models import (
    ArxmlOwner,
    ComponentKind,
    InterfaceKind,
    PortDirection,
    TriggerKind,
)
from porttool.core.operations import AddPort, AddTrigger, AnyOperation
from tests.fixtures.generator import LARGE
from tests.unit.writer.conftest import Repo, errors, is_insertion_only, make_repo


def large_operations(repo: Repo, files: int = 100) -> list[AnyOperation]:
    """Two operations per component for ``files`` DaVinci-owned components: 200 in 100 files."""
    model = repo.model
    interface = next(
        i for i in model.interfaces.values() if i.kind is InterfaceKind.SENDER_RECEIVER
    )
    operations: list[AnyOperation] = []
    for component in model.components.values():
        if component.owner is not ArxmlOwner.DAVINCI or component.kind is ComponentKind.COMPOSITION:
            continue
        runnables = model.runnables_of(component.path)
        if not runnables:
            continue
        number = len(operations) // 2
        operations.append(
            AddPort(
                id=f"port-{number}",
                swc_path=component.path,
                name="RpBenchmark",
                direction=PortDirection.R,
                interface_path=interface.path,
            )
        )
        operations.append(
            AddTrigger(
                id=f"trigger-{number}",
                swc_path=component.path,
                runnable_name=runnables[0].name,
                kind=TriggerKind.DATA_RECEIVED,
                port_name="RpBenchmark",
                target=interface.elements[0].name,
            )
        )
        if len(operations) == 2 * files:
            break
    return operations


@pytest.mark.slow
def test_200_operations_in_100_files_of_the_large_repo_within_5_seconds(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, LARGE)
    operations = large_operations(repo)
    assert len(operations) == 200

    started = time.perf_counter()
    plan = repo.plan(*operations)
    plan_s = time.perf_counter() - started
    assert not plan.findings and len(plan.files) == 100
    before = {item.rel_path: repo.read(item.rel_path) for item in plan.files}

    started = time.perf_counter()
    outcome = repo.apply_plan(plan)
    apply_s = time.perf_counter() - started
    assert not errors(outcome.findings)
    assert len(outcome.written) == 100 and len(outcome.applied_operation_ids) == 200
    megabytes = sum(len(before[name]) for name in outcome.written) / 1e6
    print(
        f"\n[writer] LARGE: 200 operations, 100 files ({megabytes:.1f} MB): "
        f"plan {plan_s:.2f} s, apply {apply_s:.2f} s"
    )
    for name, old in before.items():
        new = repo.read(name)
        assert len(new) > len(old) and is_insertion_only(old, new), name
    assert apply_s < 5.0
    assert plan_s < 5.0
