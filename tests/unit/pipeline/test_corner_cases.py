"""Corner cases of the apply that are not about preflight or the DaVinci steps."""

from __future__ import annotations

from pathlib import Path

from porttool.core.pending import PendingQueue
from porttool.core.results import RequestStatus, Severity
from porttool.pipeline import PipelineError, codes
from porttool.pipeline.jobs import JobLog
from tests.unit.pipeline.crash_child import ROWS
from tests.unit.pipeline.support import (
    FakeWriter,
    Harness,
    Repo,
    connector_row,
    make_repo,
    make_service,
    port_row,
)

SWC1 = "Config/Developer/ComponentTypes/Swc1.arxml"


def _codes(harness: Harness) -> list[str]:
    report = harness.service.last_report()
    assert report is not None
    return [finding.code for finding in report.findings]


def test_verification_finds_a_missing_object_and_rolls_back(repo: Repo, tmp_path: Path) -> None:
    # The writer claims it applied the port but never wrote it.
    writer = FakeWriter(skip_ids=frozenset({"Ports:add_port:/ComponentTypes/Swc1/PpCrash"}))
    harness = make_service(repo, tmp_path, writer=writer)
    try:
        harness.dry_run(ROWS)
        before = repo.snapshot()
        view = harness.apply()
        report = harness.service.last_report()
        assert view.state == "done" and report is not None
        assert not report.ok and report.rolled_back
        missing = [f for f in report.findings if f.code in {codes.VER_MISSING, codes.VER_MISMATCH}]
        assert any("add port /ComponentTypes/Swc1/PpCrash" in f.message for f in missing)
        assert repo.snapshot() == before
        model = harness.service.model()
        assert model is not None and "/ComponentTypes/Swc2/RpCrash" not in model.ports
        assert [r.status for r in report.results][0] is RequestStatus.ACCEPTED  # not APPLIED
    finally:
        harness.service.close()


def test_verification_finds_an_object_that_was_lost_and_rolls_back(
    repo: Repo, tmp_path: Path
) -> None:
    # The writer destroys an existing port while it adds the new ones.
    writer = FakeWriter(destroy_ports=(("/ComponentTypes/Swc1", "PpSig3"),))
    harness = make_service(repo, tmp_path, writer=writer)
    try:
        harness.dry_run(ROWS)
        before = repo.snapshot()
        harness.apply()
        report = harness.service.last_report()
        assert report is not None and not report.ok and report.rolled_back
        lost = next(f for f in report.findings if f.code == codes.VER_LOST)
        assert "/ComponentTypes/Swc1/PpSig3" in lost.message
        assert repo.snapshot() == before
    finally:
        harness.service.close()


def test_held_connector_is_released_by_a_later_apply_and_the_queue_shrinks(
    harness: Harness,
) -> None:
    repo, service = harness.repo, harness.service
    harness.dry_run(
        [
            port_row("Swc3", "RpModel", "R", "IfSig1", 1),
            connector_row("Swc1", "PpSig1", "Swc3", "RpModel", 2),
        ]
    )
    harness.apply()
    first = service.last_report()
    assert first is not None and first.ok
    pending = repo.root / "porttool/pending.json"
    assert len(PendingQueue.from_json(pending.read_text("utf-8")).entries) == 2

    # The model owner delivers the port: the SWC file changes outside PortTool.
    swc3 = repo.root / "Config/Developer/ComponentTypes/Swc3.arxml"
    port = (
        "            <R-PORT-PROTOTYPE>\n              <SHORT-NAME>RpModel</SHORT-NAME>\n"
        '              <REQUIRED-INTERFACE-TREF DEST="SENDER-RECEIVER-INTERFACE">'
        "/PortInterfaces/IfSig1</REQUIRED-INTERFACE-TREF>\n            </R-PORT-PROTOTYPE>\n"
    )
    text = swc3.read_text(encoding="utf-8")
    swc3.write_text(text.replace("          </PORTS>\n", port + "          </PORTS>\n", 1), "utf-8")
    repo.git("add", "--all", "Config")
    repo.git("commit", "--quiet", "-m", "model owner delivered RpModel")

    # A dry run without new rows reviews the queue: the port entry closes, the connector
    # is released and applied now. The queue is stored only as part of that apply.
    harness.dry_run([])
    outcome = service.last_validation()
    assert outcome is not None
    assert [op.op for op in outcome.change_set.operations] == ["add_connector"]
    assert len(PendingQueue.from_json(pending.read_text("utf-8")).entries) == 2  # untouched
    harness.apply()
    second = service.last_report()
    assert second is not None and second.ok, second
    assert PendingQueue.from_json(pending.read_text("utf-8")).entries == ()
    model = service.model()
    assert model is not None
    assert "/ComponentTypes/EcuComposition/Swc1_PpSig1_Swc3_RpModel" in model.connectors
    committed = repo.git("show", "--name-only", "--format=", "HEAD").split()
    assert sorted(committed) == [
        "Config/Developer/ComponentTypes/EcuComposition.arxml",
        "porttool/pending.json",
    ]


def test_ecu_project_in_a_subfolder_of_the_git_work_tree(tmp_path: Path) -> None:
    outer = tmp_path / "outer"
    (outer / "docs").mkdir(parents=True)
    (outer / "docs/readme.txt").write_text("other content of the work tree\n", encoding="utf-8")
    repo = make_repo(outer / "ecu/project", tmp_path / "home", git_root=outer)
    fail = [True]

    def hook(name: str, moment: str) -> None:
        if (name, moment) == ("commit", "start") and fail[0]:
            raise RuntimeError("injected")

    harness = make_service(repo, tmp_path, step_hook=hook)
    try:
        (outer / "scratch.txt").write_text("untracked, outside the project\n", encoding="utf-8")
        harness.dry_run(ROWS)
        before = repo.snapshot()
        harness.apply()
        assert repo.snapshot() == before  # rolled back with paths relative to the subfolder
        fail[0] = False
        # git restored the files, so they are newer than the model: an apply of the old
        # dry run is refused as stale. A new dry run reloads by itself.
        harness.apply()
        assert codes.PRE_STALE in _codes(harness)
        harness.dry_run(ROWS)
        harness.apply()
        report = harness.service.last_report()
        assert report is not None and report.ok, report
        assert codes.PRE_UNTRACKED_OTHER in _codes(harness)
        committed = repo.git("show", "--name-only", "--format=", "HEAD").split()
        assert f"ecu/project/{SWC1}" in committed
        assert "ecu/project/porttool/pending.json" in committed
        assert (outer / "scratch.txt").exists()
    finally:
        harness.service.close()


def test_commit_reports_true_commits_the_report_folder(tmp_path: Path) -> None:
    repo = make_repo(
        tmp_path / "repo", tmp_path / "home", extra_toml="\n[pipeline]\ncommit_reports = true\n"
    )
    harness = make_service(repo, tmp_path)
    try:
        harness.dry_run(ROWS)
        harness.apply()
        report = harness.service.last_report()
        assert report is not None and report.ok and report.branch is not None
        committed = repo.git("show", "--name-only", "--format=", "HEAD").split()
        folder = f"porttool_reports/{report.branch}"
        assert f"{folder}/report.html" in committed
        assert f"{folder}/model_requests/Team1/requests.csv" in committed
        assert repo.status() == ""  # nothing is left untracked
        assert codes.REP_UNTRACKED not in _codes(harness)
    finally:
        harness.service.close()


def test_report_dir_outside_the_repository_is_used_for_success_and_failure(
    tmp_path: Path,
) -> None:
    reports = tmp_path / "shared reports"
    toml = f"\n[pipeline]\nreport_dir = '{reports.as_posix()}'\n"
    repo = make_repo(tmp_path / "repo", tmp_path / "home", extra_toml=toml)
    fail = [True]

    def hook(name: str, moment: str) -> None:
        if (name, moment) == ("verify", "start") and fail[0]:
            raise RuntimeError("injected")

    harness = make_service(repo, tmp_path, step_hook=hook)
    try:
        harness.dry_run(ROWS)
        harness.apply()
        failed = harness.service.last_report()
        assert failed is not None and failed.report_file is not None
        assert Path(failed.report_file).is_relative_to(reports)
        fail[0] = False
        harness.dry_run(ROWS)
        harness.apply()
        report = harness.service.last_report()
        assert report is not None and report.ok and report.report_file is not None
        assert Path(report.report_file) == reports / str(report.branch) / "report.html"
        assert repo.status() == ""  # no report inside the repository
    finally:
        harness.service.close()


def test_report_folder_ignored_by_git_gives_no_hint_and_a_clean_status(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo", tmp_path / "home", gitignore="porttool_reports/\n")
    harness = make_service(repo, tmp_path)
    try:
        harness.dry_run(ROWS)
        harness.apply()
        report = harness.service.last_report()
        assert report is not None and report.ok
        assert codes.REP_UNTRACKED not in _codes(harness)
        assert repo.status() == ""
        # The next apply starts from a clean tree: no warning about the old reports.
        harness.dry_run([port_row("Swc2", "PpNext", "P", "IfSig1")])
        harness.apply()
        assert codes.PRE_UNTRACKED_OTHER not in _codes(harness)
        second = harness.service.last_report()
        assert second is not None and second.ok
        assert not [f for f in second.findings if f.severity is Severity.ERROR]
    finally:
        harness.service.close()


def test_delta_that_cannot_be_built_is_a_warning_and_the_request_sheet_is_still_written(
    repo: Repo, tmp_path: Path
) -> None:
    from tests.unit.pipeline.support import error_finding

    writer = FakeWriter(delta_findings=(error_finding("WRT-DELTA-1", "unsupported"),))
    harness = make_service(repo, tmp_path, writer=writer)
    try:
        harness.dry_run(ROWS)
        harness.apply()
        report = harness.service.last_report()
        assert report is not None and report.ok and report.report_file is not None
        warning = next(f for f in report.findings if f.code == codes.PKG_DELTA)
        assert warning.severity is Severity.WARNING and "WRT-DELTA-1" in warning.message
        assert (Path(report.report_file).parent / "model_requests/Team1/requests.csv").is_file()
    finally:
        harness.service.close()


def test_open_repo_again_resets_the_results_and_is_refused_while_an_apply_runs(
    harness: Harness,
) -> None:
    harness.dry_run(ROWS)
    assert harness.service.last_validation() is not None
    harness.service.open_repo(harness.repo.root)
    assert harness.service.model() is None and harness.service.last_validation() is None
    assert harness.service.last_plan() is None


def test_close_cancels_a_running_apply_and_the_rollback_still_finishes(
    repo: Repo, tmp_path: Path
) -> None:
    import threading

    reached = threading.Event()

    def hook(name: str, moment: str) -> None:
        if (name, moment) == ("verify", "start"):
            reached.set()
            threading.Event().wait(0.3)  # the job is busy while close() is called

    harness = make_service(repo, tmp_path, step_hook=hook)
    harness.dry_run(ROWS)
    before = repo.snapshot()
    harness.service.start_apply()
    assert reached.wait(60)
    harness.service.close()  # cancels the job; returns after the restoration has run
    assert repo.snapshot() == before
    assert not harness.journal_file().exists()
    try:
        harness.service.start_load()
    except PipelineError as error:
        assert "shutting down" in str(error)
    else:
        raise AssertionError("a closed service must refuse new jobs")


def test_job_log_is_bounded_and_can_be_polled() -> None:
    log = JobLog(max_lines=3)
    for number in range(5):
        log.add(f"line {number}\n")
    log.add("   ")
    assert log.tail() == ("line 2", "line 3", "line 4")
    assert log.since(0) == (("line 2", "line 3", "line 4"), 5)
    assert log.since(4) == (("line 4",), 5)
    assert log.since(5) == ((), 5)


def test_wait_for_an_unknown_job_is_a_clear_error(harness: Harness) -> None:
    try:
        harness.service.wait("nope")
    except PipelineError as error:
        assert "Unknown job" in str(error)
    else:
        raise AssertionError("expected a PipelineError")
    assert harness.service.job_log("nope") == ((), 0)
