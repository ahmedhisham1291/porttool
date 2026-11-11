"""Service skeleton: open, load, export, dry run, job views."""

from __future__ import annotations

import json
from pathlib import Path

import openpyxl
import pytest

from porttool.core.errors import ConfigError
from porttool.core.interfaces import AppService
from porttool.core.operations import Route
from porttool.core.results import RequestStatus, Severity
from porttool.core.sheets import COLUMNS, Sheet
from porttool.pipeline import PipelineError, build_service, codes
from tests.unit.pipeline.support import Harness, Repo, connector_row, make_service, port_row


def test_service_satisfies_the_app_service_protocol(harness: Harness) -> None:
    service: AppService = harness.service
    assert service.config() is not None
    assert service.model() is None
    assert service.model_is_stale() is False
    assert service.recovery_needed() is False
    assert service.last_validation() is None and service.last_plan() is None
    assert service.last_report() is None
    assert service.job("nope") is None and service.cancel("nope") is False


def test_start_methods_are_refused_before_a_repo_is_open(tmp_path: Path) -> None:
    service = build_service()
    try:
        with pytest.raises(PipelineError, match="Open a repository"):
            service.start_load()
        with pytest.raises(ConfigError):
            service.open_repo(tmp_path / "missing")
    finally:
        service.close()


def test_load_swaps_the_model_and_reports_a_summary(harness: Harness) -> None:
    view = harness.run(harness.service.start_load())
    assert view.state == "done" and view.name == "load"
    assert view.summary is not None
    model = harness.service.model()
    assert model is not None
    assert view.summary["ports"] == len(harness.repo.truth.ports) == len(model.ports)
    json.dumps(view.summary)  # small and JSON-able
    assert view.log_tail  # step messages are kept
    assert harness.service.model_is_stale() is False
    assert [job.id for job in harness.service.jobs()] == [view.id]


def test_model_is_stale_after_a_file_changed(harness: Harness) -> None:
    harness.run(harness.service.start_load())
    swc = harness.repo.root / "Config/Developer/ComponentTypes/Swc1.arxml"
    swc.write_text(swc.read_text(encoding="utf-8") + "\n<!-- edit -->\n", encoding="utf-8")
    harness.service._stale_cache = None  # the answer is cached for two seconds
    assert harness.service.model_is_stale() is True


def test_export_writes_a_workbook_with_meta_from_model_and_git(
    harness: Harness, tmp_path: Path
) -> None:
    target = tmp_path / "out.xlsx"
    view = harness.run(harness.service.start_export(target))
    assert view.state == "done" and view.summary is not None and view.summary["ok"] is True
    assert view.summary["baseline_commit"] == harness.repo.head()
    workbook = openpyxl.load_workbook(target)
    meta = {row[0].value: row[1].value for row in workbook["Meta"].iter_rows() if row[0].value}
    model = harness.service.model()
    assert model is not None  # the export loaded the model itself
    assert harness.repo.head() in meta.values() and model.model_hash in meta.values()
    assert harness.repo.status() == ""  # the export changed nothing in the repo


def test_dry_run_from_rows_accepts_routes_and_plans(harness: Harness) -> None:
    rows = [
        port_row("Swc1", "PpNew", "P", "IfSig1", 1),
        port_row("Swc3", "RpModel", "R", "IfSig1", 2),  # Swc3 is model-owned
        connector_row("Swc1", "PpNew", "Swc3", "RpModel", 3),  # depends on the model port
        port_row("Swc1", "bad name!", "P", "IfSig1", 4),
    ]
    view = harness.dry_run(rows)
    outcome, plan = harness.service.last_validation(), harness.service.last_plan()
    assert outcome is not None and plan is not None and view.summary is not None
    statuses = [result.status for result in outcome.results]
    assert statuses == [
        RequestStatus.ACCEPTED,
        RequestStatus.WAITING_FOR_MODEL_OWNER,
        RequestStatus.ON_HOLD,
        RequestStatus.REJECTED,
    ]
    assert view.summary["operations"] == {"apply": 1, "model": 1, "hold": 1}
    # Only the "apply" operations are planned.
    assert [op.route for op in plan.change_set.operations] == [Route.APPLY]
    assert [file.rel_path for file in plan.files] == ["Config/Developer/ComponentTypes/Swc1.arxml"]
    assert len(outcome.pending.entries) == 2
    assert view.summary["can_apply"] is True and view.summary["nothing_to_do"] is False
    assert harness.repo.status() == ""  # a dry run changes nothing
    assert not (harness.repo.root / "porttool/pending.json").exists()


def test_dry_run_from_an_exported_and_edited_workbook(harness: Harness, tmp_path: Path) -> None:
    exported = tmp_path / "requests.xlsx"
    assert harness.run(harness.service.start_export(exported)).state == "done"
    workbook = openpyxl.load_workbook(exported)
    sheet = workbook[Sheet.PORTS.value]
    keys = [column.key for column in COLUMNS[Sheet.PORTS]]
    blank = next(n for n in range(2, 5000) if sheet.cell(n, 2).value is None)
    cells = {
        "swc": "Swc2",
        "port": "PpFromExcel",
        "direction": "P",
        "interface": "IfSig1",
        "init_value": "none",
        "action": "ADD",
        "requester": "Team1",
    }
    for key, value in cells.items():
        sheet.cell(blank, keys.index(key) + 1).value = value
    edited = tmp_path / "edited.xlsx"
    workbook.save(edited)
    view = harness.run(harness.service.start_dry_run([edited]))
    assert view.state == "done", view
    outcome = harness.service.last_validation()
    assert outcome is not None
    assert [result.status for result in outcome.results] == [RequestStatus.ACCEPTED]
    assert outcome.results[0].row is not None and outcome.results[0].row.row == blank
    (operation,) = outcome.change_set.operations
    assert operation.op == "add_port" and operation.source_file == str(edited)


def test_dry_run_rejects_rows_with_import_errors_before_validation(
    harness: Harness, tmp_path: Path
) -> None:
    exported = tmp_path / "requests.xlsx"
    harness.run(harness.service.start_export(exported))
    workbook = openpyxl.load_workbook(exported)
    sheet = workbook[Sheet.PORTS.value]
    keys = [column.key for column in COLUMNS[Sheet.PORTS]]
    blank = next(n for n in range(2, 5000) if sheet.cell(n, 2).value is None)
    # A new row without an Action: the workbook import reports an error for it.
    sheet.cell(blank, keys.index("swc") + 1).value = "Swc2"
    sheet.cell(blank, keys.index("port") + 1).value = "PpNoAction"
    edited = tmp_path / "edited.xlsx"
    workbook.save(edited)
    harness.run(harness.service.start_dry_run([edited]))
    outcome = harness.service.last_validation()
    assert outcome is not None
    (result,) = outcome.results
    assert result.status is RequestStatus.REJECTED
    assert any(f.code.startswith("WBK-") and f.severity is Severity.ERROR for f in result.findings)
    assert not outcome.change_set.operations


@pytest.mark.parametrize(
    ("content", "code"),
    [
        ("{ not json", codes.PEND_MALFORMED),
        ('{"format_version": 1, "entries": [{"oops": 1}]}', codes.PEND_MALFORMED),
        (
            '<<<<<<< HEAD\n{"entries": []}\n=======\n{"entries": []}\n>>>>>>> other\n',
            codes.PEND_CONFLICT,
        ),
    ],
    ids=["malformed", "wrong-schema", "conflict-markers"],
)
def test_pending_file_malformed_or_with_conflict_markers_gives_a_finding(
    repo: Repo, tmp_path: Path, content: str, code: str
) -> None:
    pending = repo.root / "porttool/pending.json"
    pending.parent.mkdir()
    pending.write_text(content, encoding="utf-8")
    repo.git("add", "--all")
    repo.git("commit", "--quiet", "-m", "pending")
    harness = make_service(repo, tmp_path)
    try:
        view = harness.dry_run([port_row("Swc1", "PpNew", "P", "IfSig1")])
        outcome = harness.service.last_validation()
        assert outcome is not None and view.summary is not None
        assert [f.code for f in outcome.findings if f.severity is Severity.ERROR] == [code]
        assert view.summary["can_apply"] is False
    finally:
        harness.service.close()


def test_failed_job_has_an_error_text_and_the_service_stays_alive(
    repo: Repo, tmp_path: Path
) -> None:
    harness = make_service(repo, tmp_path)
    try:
        (repo.root / "Config/Developer").rename(repo.root / "Config/Gone")
        view = harness.run(harness.service.start_export(tmp_path / "nowhere" / "x.xlsx"))
        assert view.state in {"done", "failed"}
        if view.state == "failed":
            assert view.error
        assert harness.run(harness.service.start_doctor()).state == "done"
    finally:
        harness.service.close()
