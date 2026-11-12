"""The click commands and the helper that reads the event findings."""

from __future__ import annotations

from pathlib import Path

import click
from click.testing import CliRunner

from porttool.core.operations import ChangeSet
from porttool.writer import CreatedEvent, codes, created_events, register_cli
from tests.unit.writer.conftest import CT, Repo
from tests.unit.writer.test_behavior import OPERATIONS
from tests.unit.writer.test_ports_connectors import _port

B1 = f"{CT}/Swc1/Swc1_InternalBehavior"


def _run(*arguments: str) -> tuple[int, str]:
    @click.group()
    def root() -> None:
        pass

    register_cli(root)
    result = CliRunner().invoke(root, list(arguments))
    return result.exit_code, result.output


def _json(repo: Repo, *operations: object) -> str:
    file = repo.root.parent / "changes.json"
    file.write_text(ChangeSet(operations=operations).to_json(), encoding="utf-8")  # type: ignore[arg-type]
    return str(file)


def test_created_events_are_read_back_from_the_findings(small: Repo) -> None:
    outcome = small.apply(*OPERATIONS)
    events = created_events(outcome.findings)
    assert len(events) == 5
    assert events[0] == CreatedEvent(
        operation_id="trg-Swc1_Cyclic100ms-PERIODIC",
        path=f"{B1}/TMT_Swc1_Cyclic100ms_1",
        element="TIMING-EVENT",
        runnable="Swc1_Cyclic100ms",
        task="Task_500ms",
        position_after_event=None,
        file="Config/Developer/ComponentTypes/Swc1.arxml",
    )
    assert (events[4].element, events[4].task, events[4].position_after_event) == (
        "OPERATION-INVOKED-EVENT",
        None,
        f"{B1}/OIT_PpSrv2_Op1",
    )
    assert set(small.reload().triggers) >= {event.path for event in events}
    assert created_events(f for f in outcome.findings if f.code != codes.INFO_EVENT) == ()


def test_plan_command_prints_preview_files_and_findings(small: Repo) -> None:
    before = small.snapshot()
    good = _json(small, _port(f"{CT}/Swc1", "PpNew", "P", "IfSig1"))
    code, output = _run("writer-plan", "--repo", str(small.root), good)
    assert code == 0
    assert output.splitlines() == [
        "Swc1: add P-port PpNew (IfSig1)",
        "modify Config/Developer/ComponentTypes/Swc1.arxml: add port PpNew",
    ]
    bad = _json(small, _port(f"{CT}/Swc3", "PpNew", "P", "IfSig1"))
    code, output = _run("writer-plan", "--repo", str(small.root), bad)
    assert code == 1 and output.startswith("ERROR WRT-OWNER-001: [op port-3-PpNew] Swc3 is")
    assert small.snapshot() == before


def test_delta_command_writes_the_file(small: Repo, tmp_path: Path) -> None:
    target = tmp_path / "delta" / "out.arxml"
    changes = _json(small, _port(f"{CT}/Swc3", "PpNew", "P", "IfSig1"))
    code, output = _run("writer-delta", "--repo", str(small.root), changes, str(target))
    assert code == 0 and "INFO WRT-INFO-DELTA: Delta with 1 element(s) written" in output
    assert b"PpNew" in target.read_bytes()


def test_commands_fail_with_a_message_and_exit_code_2(small: Repo, tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("{ not json", encoding="utf-8")
    code, output = _run("writer-plan", "--repo", str(small.root), str(broken))
    assert code == 2 and "[PT-E100]" in output and "is not a change set" in output
    code, output = _run("writer-plan", str(broken))
    assert code == 2 and "Pass --repo" in output
    code, output = _run("writer-delta", "--repo", str(tmp_path), str(broken), "x.arxml")
    assert code == 2 and "Traceback" not in output


def test_codes_command_lists_every_code() -> None:
    code, output = _run("writer-codes")
    listed = output.split()
    assert code == 0 and "WRT-STATE-001" in listed and "WRT-INFO-EVENT" in listed
    assert all(item.startswith("WRT-") for item in listed) and len(listed) == len(set(listed))
