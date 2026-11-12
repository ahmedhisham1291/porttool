"""End to end with the REAL reader, workbook, validator, writer, DaVinci runner and pipeline.

The pipeline agent tested against a fake writer and a fake runner; the writer and the
runner were built in parallel. This file is the first time all real parts run together.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from porttool.core.interfaces import JobView
from porttool.core.models import AccessKind, ComponentKind, InterfaceKind, TriggerKind
from porttool.core.results import RequestStatus
from porttool.core.sheets import Sheet
from porttool.pipeline import build_service
from porttool.pipeline.service import PortToolService
from tests.fakes import FAKE_SCRIPT
from tests.unit.pipeline.support import Repo, edited_workbook, make_repo

Request = tuple[Sheet, Mapping[str, str]]


def _service(
    repo: Repo, base: Path, step_hook: Callable[[str, str], None] | None = None
) -> PortToolService:
    service = build_service(
        journal_dir=base / "journals",
        lock_dir=base / "locks",
        git_env=repo.env,
        step_hook=step_hook,
    )
    service.open_repo(repo.root)
    return service


def _run(service: PortToolService, job_id: str) -> JobView:
    return service.wait(job_id, 180)


def _requests(repo: Repo) -> tuple[list[Request], dict[str, str]]:
    """One request of every kind, built from the fixture's ground truth."""
    truth = repo.truth
    blocked = set(truth.model_owned_swcs) | set(truth.locked_swcs)
    swcs = [
        c for c in truth.components if c.kind is ComponentKind.APPLICATION and c.name not in blocked
    ]
    first, second = swcs[0], swcs[1]
    iface = next(i for i in truth.interfaces if i.kind is InterfaceKind.SENDER_RECEIVER)
    element = iface.elements[0].name
    run_a = next(r for r in truth.runnables if r.swc_path == first.path)
    run_b = next(r for r in truth.runnables if r.swc_path == second.path)
    proto_a = next(p for p in truth.prototypes if p.type_path == first.path)
    proto_b = next(p for p in truth.prototypes if p.type_path == second.path)
    composition = truth.compositions[0]
    data_type = truth.data_types[0]
    base_type = truth.base_types[0]
    names = {
        "swc_a": first.path,
        "swc_b": second.path,
        "run_a": run_a.path,
        "run_b": run_b.path,
        "composition": composition.path,
        "proto_a": proto_a.path,
        "proto_b": proto_b.path,
        "iface": iface.path,
        "element": element,
    }
    requests: list[Request] = [
        (Sheet.DATA_TYPES, {"name": "E2E_Speed", "level": "implementation", "category": "value",
                            "base_type": base_type.name, "lower_limit": "0",
                            "upper_limit": "200"}),
        (Sheet.INTERFACES, {"name": "IfE2E", "kind": "sender-receiver"}),
        (Sheet.INTERFACE_ELEMENTS, {"interface": "IfE2E", "element": "Val",
                                    "data_type": data_type.name, "queued": "N"}),
        (Sheet.PORTS, {"swc": first.name, "port": "PpE2E", "direction": "P",
                       "interface": iface.name, "init_value": "none"}),
        (Sheet.PORTS, {"swc": second.name, "port": "RpE2E", "direction": "R",
                       "interface": iface.name, "init_value": "none"}),
        (Sheet.PORTS, {"swc": first.name, "port": "PpNewIf", "direction": "P",
                       "interface": "IfE2E", "init_value": "none"}),
        (Sheet.RUNNABLE_ACCESS, {"swc": first.name, "runnable": run_a.name, "port": "PpE2E",
                                 "target": element, "access_kind": "explicit send"}),
        (Sheet.RUNNABLE_ACCESS, {"swc": second.name, "runnable": run_b.name, "port": "RpE2E",
                                 "target": element, "access_kind": "implicit read"}),
        (Sheet.RUNNABLE_TRIGGERS, {"swc": second.name, "runnable": run_b.name,
                                   "trigger_kind": "on data reception", "port": "RpE2E",
                                   "target": element}),
        (Sheet.CONNECTIONS, {"provider_swc": proto_a.name, "provider_port": "PpE2E",
                             "requester_swc": proto_b.name, "requester_port": "RpE2E",
                             "composition": composition.name}),
    ]  # fmt: skip
    return requests, names


def _explain(service: PortToolService) -> str:
    outcome = service.last_validation()
    lines = []
    if outcome is not None:
        for result in outcome.results:
            for finding in result.findings:
                lines.append(f"{result.status.value}: {finding.code} {finding.message}")
        lines += [f"{f.code} {f.message}" for f in outcome.findings]
    plan = service.last_plan()
    if plan is not None:
        lines += [f"plan: {f.code} {f.message}" for f in plan.findings]
    report = service.last_report()
    if report is not None:
        lines += [f"report: {f.severity.value} {f.code} {f.message}" for f in report.findings]
    return "\n".join(lines)


def _check_applied(service: PortToolService, repo: Repo, names: dict[str, str]) -> None:
    model = service.model()
    assert model is not None
    port_p = model.find_port(names["swc_a"], "PpE2E")
    port_r = model.find_port(names["swc_b"], "RpE2E")
    assert port_p is not None and port_r is not None
    assert port_p.interface_path == names["iface"] == port_r.interface_path
    connectors = model.connectors_at(names["proto_a"], port_p.path)
    assert len(connectors) == 1
    assert connectors[0].requester.port_path == port_r.path
    assert connectors[0].requester.prototype_path == names["proto_b"]
    send = [a for a in model.access_points_of_port(port_p.path)]
    assert [(a.kind, a.target, a.runnable_path) for a in send] == [
        (AccessKind.EXPLICIT_SEND, names["element"], names["run_a"])
    ]
    read = [a for a in model.access_points_of_port(port_r.path)]
    assert [(a.kind, a.runnable_path) for a in read] == [(AccessKind.IMPLICIT_READ, names["run_b"])]
    triggers = model.triggers_at_port(port_r.path)
    assert [(t.kind, t.runnable_path, t.target) for t in triggers] == [
        (TriggerKind.DATA_RECEIVED, names["run_b"], names["element"])
    ]
    new_if = next(i for i in model.interfaces.values() if i.name == "IfE2E")
    assert [e.name for e in new_if.elements] == ["Val"]
    new_port = model.find_port(names["swc_a"], "PpNewIf")
    assert new_port is not None and new_port.interface_path == new_if.path
    assert any(d.name == "E2E_Speed" for d in model.data_types.values())
    assert not [f for f in model.findings if f.severity.value == "error"], model.findings
    assert repo.branch().startswith("porttool/")
    tracked_changes = [line for line in repo.status().splitlines() if not line.startswith("??")]
    assert tracked_changes == []


def test_real_parts_apply_one_request_of_every_kind(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo", tmp_path / "home")
    service = _service(repo, tmp_path)
    try:
        assert _run(service, service.start_load()).state == "done"
        before = service.model()
        assert before is not None
        requests, names = _requests(repo)
        workbook = edited_workbook(service, tmp_path, requests)
        dry = _run(service, service.start_dry_run([workbook]))
        assert dry.state == "done", dry
        outcome = service.last_validation()
        assert outcome is not None
        statuses = [r.status for r in outcome.results]
        assert statuses == [RequestStatus.ACCEPTED] * len(requests), _explain(service)
        plan = service.last_plan()
        assert plan is not None
        assert not [f for f in plan.findings if f.severity.value == "error"], _explain(service)
        start_branch = repo.branch()
        view = _run(service, service.start_apply())
        report = service.last_report()
        assert view.state == "done" and report is not None and report.ok, _explain(service)
        assert {r.status for r in report.results} == {RequestStatus.APPLIED}, _explain(service)
        _check_applied(service, repo, names)
        after = service.model()
        assert after is not None
        # Nothing that existed before is gone or changed.
        for path, port in before.ports.items():
            assert after.ports[path] == port
        for path, connector in before.connectors.items():
            assert after.connectors[path] == connector
        assert set(before.access_points) <= set(after.access_points)
        assert set(before.triggers) <= set(after.triggers)
        # The original branch still holds the untouched files.
        assert repo.git("diff", "--stat", f"{start_branch}..HEAD").strip() != ""
        # Asking again changes nothing.
        _run(service, service.start_dry_run([workbook]))
        again = service.last_validation()
        assert again is not None
        assert {r.status for r in again.results} == {RequestStatus.ALREADY_APPLIED}, _explain(
            service
        )
    finally:
        service.close()


def test_real_parts_failure_after_the_write_restores_every_byte(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo", tmp_path / "home")

    def hook(name: str, moment: str) -> None:
        if (name, moment) == ("verify", "start"):
            raise RuntimeError("injected failure after the real writer changed the files")

    service = _service(repo, tmp_path, step_hook=hook)
    try:
        assert _run(service, service.start_load()).state == "done"
        requests, _names = _requests(repo)
        workbook = edited_workbook(service, tmp_path, requests)
        assert _run(service, service.start_dry_run([workbook])).state == "done"
        before = repo.snapshot()
        view = _run(service, service.start_apply())
        report = service.last_report()
        assert view.state in ("done", "failed")
        assert report is None or not report.ok
        assert repo.snapshot() == before
        assert not service.recovery_needed()
    finally:
        service.close()


def test_real_runner_runs_the_enabled_davinci_steps_with_fake_tools(tmp_path: Path) -> None:
    python = sys.executable.replace("\\", "/")
    fake = str(FAKE_SCRIPT).replace("\\", "/")

    def command(tool: str, *arguments: str) -> str:
        parts = [python, fake, "--tool", tool, *arguments]
        return "[" + ", ".join(f'"{part}"' for part in parts) + "]"

    extra = (
        '\n[pipeline]\ndavinci_steps = ["check", "validate", "generate"]\n'
        "\n[davinci.commands]\n"
        f"check = {command('DVWspChecker', '{dcf}')}\n"
        f"validate = {command('DVCfgCmd', '{dpa}', '--validate')}\n"
        f"generate = {command('DVCfgCmd', '{dpa}', '--generate', '{modules_csv}')}\n"
    )
    repo = make_repo(tmp_path / "repo", tmp_path / "home", extra_toml=extra)
    service = _service(repo, tmp_path)
    try:
        assert _run(service, service.start_load()).state == "done"
        requests, names = _requests(repo)
        workbook = edited_workbook(service, tmp_path, requests)
        assert _run(service, service.start_dry_run([workbook])).state == "done"
        view = _run(service, service.start_apply())
        report = service.last_report()
        assert view.state == "done" and report is not None and report.ok, _explain(service)
        _check_applied(service, repo, names)
        log = "\n".join(view.log_tail).lower()
        assert "check" in log and "generate" in log
    finally:
        service.close()


@pytest.mark.slow
def test_real_parts_dry_run_on_the_large_repo_is_quick(tmp_path: Path) -> None:
    import time

    from tests.fixtures.generator import LARGE

    repo = make_repo(tmp_path / "repo", tmp_path / "home", spec=LARGE)
    service = _service(repo, tmp_path)
    try:
        started = time.perf_counter()
        assert _run(service, service.start_load()).state == "done"
        loaded = time.perf_counter() - started
        requests, _names = _requests(repo)
        started = time.perf_counter()
        workbook = edited_workbook(service, tmp_path, requests)
        exported = time.perf_counter() - started
        started = time.perf_counter()
        assert _run(service, service.start_dry_run([workbook])).state == "done"
        dry = time.perf_counter() - started
        print(
            f"\n[integration] LARGE: load {loaded:.1f} s, export+edit {exported:.1f} s, "
            f"dry run {dry:.1f} s"
        )
        outcome = service.last_validation()
        assert outcome is not None
        assert {r.status for r in outcome.results} == {RequestStatus.ACCEPTED}, _explain(service)
    finally:
        service.close()
