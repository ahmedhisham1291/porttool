"""The apply transaction when everything works."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import openpyxl

from porttool.core.pending import PendingQueue
from porttool.core.results import RequestStatus, Severity
from porttool.core.sheets import COLUMNS, Sheet
from porttool.pipeline import codes
from porttool.pipeline.journal import read_journal
from tests.unit.pipeline.support import Harness, Repo, connector_row, make_service, port_row


def _codes(harness: Harness, severity: Severity | None = None) -> list[str]:
    report = harness.service.last_report()
    assert report is not None
    return [f.code for f in report.findings if severity is None or f.severity is severity]


def test_apply_edits_on_a_new_branch_verifies_and_commits(harness: Harness) -> None:
    repo = harness.repo
    start = repo.head()
    harness.dry_run([port_row("Swc1", "PpNew", "P", "IfSig1")])
    old_model = harness.service.model()
    view = harness.apply()
    assert view.state == "done", view
    report = harness.service.last_report()
    assert report is not None and report.ok and not report.rolled_back
    today = datetime.now().strftime("%Y%m%d")
    assert report.branch == f"porttool/{today}-1" == repo.branch()
    # One commit on the new branch; main is untouched.
    assert repo.git("rev-parse", "HEAD~1").strip() == start
    assert repo.git("rev-parse", "main").strip() == start
    assert repo.git("show", "--stat", "--format=%s%n%b", "HEAD").count("Swc1.arxml") == 1
    message = repo.git("log", "-1", "--format=%B")
    assert "add port /ComponentTypes/Swc1/PpNew" in message and "Team1" in message
    # Verified by reload: the service now holds the new model.
    model = harness.service.model()
    assert model is not None and model is not old_model
    assert "/ComponentTypes/Swc1/PpNew" in model.ports
    assert harness.service.model_is_stale() is False
    # Results became APPLIED; the default run skipped every DaVinci step and says so.
    outcome = harness.service.last_validation()
    assert outcome is not None
    assert [r.status for r in outcome.results] == [RequestStatus.APPLIED]
    assert [r.status for r in report.results] == [RequestStatus.APPLIED]
    assert codes.APPLY_STEPS_SKIPPED in _codes(harness, Severity.WARNING)
    assert harness.runner.calls == []
    assert view.summary is not None
    assert view.summary["ok"] is True and view.summary["branch"] == report.branch
    assert view.summary["skipped_steps"] == ["check", "script", "validate", "generate"]
    assert view.summary["commit"] == repo.head()
    json.dumps(view.summary)
    # The report exists, is not committed, and the only untracked files are the reports.
    assert report.report_file is not None and Path(report.report_file).is_file()
    assert Path(report.report_file).parent == repo.root / "porttool_reports" / report.branch
    assert all(line[3:].startswith("porttool_reports/") for line in repo.status().splitlines())
    # The journal is closed and archived; nothing needs recovery.
    assert not harness.journal_file().exists()
    archived = list((harness.journal_file().parent / "archive").glob("*-committed.jsonl"))
    assert len(archived) == 1
    state = read_journal(archived[0])
    assert state is not None and state.closed == "committed" and state.original_branch == "main"
    assert state.original_commit == start and state.commit == repo.head()
    assert [p.rel_path for p in state.paths][0] == "Config/Developer/ComponentTypes/Swc1.arxml"
    assert ("write", "started") in state.steps and ("write", "finished") in state.steps
    assert harness.service.recovery_needed() is False
    # Step messages reached the job log.
    assert any("Committed" in line for line in view.log_tail)


def test_a_second_apply_of_the_same_dry_run_is_refused(harness: Harness) -> None:
    harness.dry_run([port_row("Swc1", "PpNew", "P", "IfSig1")])
    assert harness.apply().summary == harness.service.job(harness.service.jobs()[-1].id).summary  # type: ignore[union-attr]
    head = harness.repo.head()
    view = harness.apply()
    assert view.state == "done" and view.summary is not None and view.summary["ok"] is False
    assert codes.PRE_NO_DRY_RUN in _codes(harness, Severity.ERROR)
    assert harness.repo.head() == head


def test_full_cycle_export_edit_dry_run_apply_verify_on_the_small_repo(
    harness: Harness, tmp_path: Path
) -> None:
    """export, scripted edit, dry run, apply, verify: real reader, workbook and validator."""
    service, repo = harness.service, harness.repo
    exported = tmp_path / "requests.xlsx"
    assert harness.run(service.start_export(exported)).state == "done"
    workbook = openpyxl.load_workbook(exported)

    def add(sheet: Sheet, **cells: str) -> None:
        worksheet = workbook[sheet.value]
        keys = [column.key for column in COLUMNS[sheet]]
        blank = next(n for n in range(2, 5000) if worksheet.cell(n, 2).value is None)
        for key, value in cells.items():
            worksheet.cell(blank, keys.index(key) + 1).value = value

    add(Sheet.PORTS, swc="Swc1", port="PpExcel", direction="P", interface="IfSig1",
        init_value="none", action="ADD", requester="Team1")  # fmt: skip
    add(Sheet.PORTS, swc="Swc2", port="RpExcel", direction="R", interface="IfSig1",
        init_value="none", action="ADD", requester="Team1")  # fmt: skip
    add(Sheet.CONNECTIONS, provider_swc="Swc1", provider_port="PpExcel", requester_swc="Swc2",
        requester_port="RpExcel", composition="EcuComposition", action="ADD",
        requester="Team1")  # fmt: skip
    edited = tmp_path / "edited.xlsx"
    workbook.save(edited)

    dry = harness.run(service.start_dry_run([edited]))
    assert dry.state == "done" and dry.summary is not None
    assert dry.summary["by_status"] == {"accepted": 3}, service.last_validation()
    plan = service.last_plan()
    assert plan is not None and len(plan.files) == 3
    view = harness.apply()
    report = service.last_report()
    assert view.state == "done" and report is not None and report.ok, report
    assert [r.status for r in report.results] == [RequestStatus.APPLIED] * 3
    model = service.model()
    assert model is not None
    assert "/ComponentTypes/Swc1/PpExcel" in model.ports
    assert "/ComponentTypes/Swc2/RpExcel" in model.ports
    connector = model.connectors["/ComponentTypes/EcuComposition/Swc1_PpExcel_Swc2_RpExcel"]
    assert connector.provider.port_path == "/ComponentTypes/Swc1/PpExcel"
    changed = repo.git("show", "--name-only", "--format=", "HEAD").split()
    assert sorted(changed) == [
        "Config/Developer/ComponentTypes/EcuComposition.arxml",
        "Config/Developer/ComponentTypes/Swc1.arxml",
        "Config/Developer/ComponentTypes/Swc2.arxml",
    ]
    # A second export shows the applied results on the Results sheet.
    again = tmp_path / "after.xlsx"
    assert harness.run(service.start_export(again)).state == "done"
    results = openpyxl.load_workbook(again)[Sheet.RESULTS.value]
    assert [row[2].value for row in results.iter_rows(min_row=2) if row[0].value] == ["applied"] * 3
    # The same workbook again: everything is already applied, nothing to do, no new branch.
    branches = repo.branches()
    harness.run(service.start_dry_run([edited]))
    outcome = service.last_validation()
    assert outcome is not None
    assert {r.status for r in outcome.results} == {RequestStatus.ALREADY_APPLIED}
    view = harness.apply()
    assert view.summary is not None and view.summary["ok"] is True
    assert codes.APPLY_NOTHING in _codes(harness)
    assert repo.branches() == branches


def test_empty_change_set_does_nothing_and_creates_no_branch(harness: Harness) -> None:
    repo = harness.repo
    before = repo.snapshot()
    harness.dry_run([])
    view = harness.apply()
    report = harness.service.last_report()
    assert view.state == "done" and report is not None
    assert report.ok and report.branch is None and not report.rolled_back
    assert _codes(harness) == [codes.APPLY_NOTHING]
    assert repo.snapshot() == before
    assert not harness.journal_file().parent.exists() or not harness.journal_file().exists()


def test_change_set_with_only_model_and_hold_operations_updates_pending_and_packages(
    harness: Harness,
) -> None:
    repo = harness.repo
    harness.dry_run(
        [
            port_row("Swc3", "RpModel", "R", "IfSig1", 1),  # model-owned SWC
            connector_row("Swc1", "PpSig1", "Swc3", "RpModel", 2),  # held: port is missing
        ]
    )
    outcome = harness.service.last_validation()
    assert outcome is not None
    assert [r.status for r in outcome.results] == [
        RequestStatus.WAITING_FOR_MODEL_OWNER,
        RequestStatus.ON_HOLD,
    ]
    view = harness.apply()
    report = harness.service.last_report()
    assert view.state == "done" and report is not None and report.ok, report
    assert harness.writer.apply_calls == 0  # no workspace write
    assert codes.APPLY_NO_WORKSPACE_WRITE in _codes(harness)
    assert codes.APPLY_STEPS_SKIPPED not in _codes(harness)
    # Only the pending file was committed.
    assert repo.git("show", "--name-only", "--format=", "HEAD").split() == ["porttool/pending.json"]
    queue = PendingQueue.from_json((repo.root / "porttool/pending.json").read_text("utf-8"))
    assert len(queue.entries) == 2
    # Statuses stay as they are; the packages are in the report folder.
    assert [r.status for r in report.results] == [
        RequestStatus.WAITING_FOR_MODEL_OWNER,
        RequestStatus.ON_HOLD,
    ]
    assert report.report_file is not None
    folder = Path(report.report_file).parent
    sheet = folder / "model_requests" / "Team1" / "requests.csv"
    assert sheet.is_file() and (folder / "model_requests" / "Team1" / "delta.arxml").is_file()
    text = sheet.read_text(encoding="utf-8-sig")
    assert "RpModel" in text and "waiting for model owner" in text
    assert "on hold" in (folder / "held_requests.csv").read_text(encoding="utf-8-sig")
    assert codes.PKG_WRITTEN in _codes(harness)
    # The model was not reloaded (nothing in the workspace changed) and is not stale.
    assert harness.service.model_is_stale() is False


def test_branch_name_already_exists_takes_the_next_number(repo: Repo, tmp_path: Path) -> None:
    today = datetime.now().strftime("%Y%m%d")
    repo.git("branch", f"porttool/{today}-1")
    repo.git("branch", f"porttool/{today}-4")
    harness = make_service(repo, tmp_path)
    try:
        harness.dry_run([port_row("Swc1", "PpNew", "P", "IfSig1")])
        harness.apply()
        report = harness.service.last_report()
        assert report is not None and report.ok
        assert report.branch == f"porttool/{today}-5" == repo.branch()
    finally:
        harness.service.close()


def test_detached_head_is_allowed(repo: Repo, tmp_path: Path) -> None:
    repo.git("checkout", "--quiet", "--detach", "HEAD")
    harness = make_service(repo, tmp_path)
    try:
        harness.dry_run([port_row("Swc1", "PpNew", "P", "IfSig1")])
        harness.apply()
        report = harness.service.last_report()
        assert report is not None and report.ok, report
        assert repo.branch() == report.branch
    finally:
        harness.service.close()


def test_repo_path_with_spaces_and_non_ascii_characters(tmp_path: Path) -> None:
    from tests.unit.pipeline.support import make_repo

    repo = make_repo(tmp_path / "Projekt Größe 日本" / "my repo", tmp_path / "home")
    harness = make_service(repo, tmp_path / "state dir ü")
    try:
        harness.dry_run(
            [port_row("Swc1", "PpNew", "P", "IfSig1", 1), port_row("Swc3", "RpM", "R", "IfSig1", 2)]
        )
        view = harness.apply()
        report = harness.service.last_report()
        assert view.state == "done" and report is not None and report.ok, report
        assert report.report_file is not None and "Größe" in report.report_file
        assert "PpNew" in (repo.root / "Config/Developer/ComponentTypes/Swc1.arxml").read_text(
            "utf-8"
        )
        assert Path(report.report_file).read_text(encoding="utf-8").startswith("<!DOCTYPE html>")
    finally:
        harness.service.close()
