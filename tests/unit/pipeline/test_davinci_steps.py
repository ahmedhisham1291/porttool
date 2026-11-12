"""The optional DaVinci steps of the apply: check, fallback import, script, validate, generate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from porttool.core.config import ProjectConfig
from porttool.core.interfaces import DaVinciRunner
from porttool.core.results import Finding, Severity
from porttool.pipeline import PipelineError, codes
from porttool.pipeline.journal import read_journal
from tests.unit.pipeline.crash_child import ROWS, tool_writes
from tests.unit.pipeline.support import (
    FakeRunner,
    FakeWriter,
    Harness,
    Repo,
    error_finding,
    make_repo,
    make_service,
    port_row,
    tool_result,
    trigger_row,
)
from tests.unit.pipeline.test_rollback import _kill_child

ALL_STEPS = '\n[pipeline]\ndavinci_steps = ["generate", "check", "validate", "script"]\n'
TRIGGER_ROWS = [
    port_row("Swc1", "PpNew", "P", "IfSig1", 1),
    trigger_row("Swc1", "Swc1_Cyclic10ms", "20", 2),
]
SWC1 = "Config/Developer/ComponentTypes/Swc1.arxml"


def _repo(tmp_path: Path, toml: str, gitignore: str | None = None) -> Repo:
    return make_repo(tmp_path / "repo", tmp_path / "home", extra_toml=toml, gitignore=gitignore)


def _codes(harness: Harness) -> list[str]:
    report = harness.service.last_report()
    assert report is not None
    return [finding.code for finding in report.findings]


def _html(harness: Harness) -> str:
    report = harness.service.last_report()
    assert report is not None and report.report_file is not None
    return Path(report.report_file).read_text(encoding="utf-8")


def test_enabled_steps_run_in_fixed_order_and_the_script_gets_the_new_triggers(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path, ALL_STEPS)
    runner = FakeRunner(log_file=tmp_path / "tool.log")
    harness = make_service(repo, tmp_path, runner=runner)
    try:
        harness.dry_run(TRIGGER_ROWS)
        view = harness.apply()
        report = harness.service.last_report()
        assert view.state == "done" and report is not None and report.ok, report
        assert runner.calls == ["check", "script", "validate", "generate"]
        assert view.summary is not None and view.summary["skipped_steps"] == []
        assert codes.APPLY_STEPS_SKIPPED not in _codes(harness)
        assert codes.DV_MANUAL_MAPPING not in _codes(harness)  # the script mapped them
        (request,) = [json.loads(text) for text in runner.script_requests]
        (mapping,) = request["task_mappings"]
        behavior = "/ComponentTypes/Swc1/Swc1_InternalBehavior"
        assert mapping["event_path"] == f"{behavior}/TMT_Swc1_Cyclic10ms_20ms"
        assert mapping["runnable"] == "Swc1_Cyclic10ms" and mapping["task"] == "Task_10ms"
        assert mapping["position_after_event"] == f"{behavior}/TMT_Swc1_Cyclic10ms"
        assert request["apply_solving_actions"] is True
        # Tool lines reach the job log; log excerpts and the mapping reach the report.
        assert any("[DVWspChecker] fake output of check" in line for line in view.log_tail)
        html = _html(harness)
        assert "line 2 of generate" in html and "script_result.json" in html
        assert "The script task mapped these events" in html and "TMT_Swc1_Cyclic10ms_20ms" in html
        # Before the first tool ran, the journal named the folders the tools may write.
        archive = harness.journal_file().parent / "archive"
        state = read_journal(next(archive.glob("*-committed.jsonl")))
        assert state is not None
        assert {scope.rel_path for scope in state.scopes} == {
            "Config/Developer",
            "Config/ECUC",
            "Config/Synthetic.dpa",
            "Config/Developer/Synthetic.dcf",
            "Appl/GenData",
        }
    finally:
        harness.service.close()


def test_script_step_disabled_puts_the_runners_manual_instructions_into_the_report(
    repo: Repo, tmp_path: Path
) -> None:
    harness = make_service(repo, tmp_path)
    try:
        harness.dry_run(TRIGGER_ROWS)
        harness.apply()
        report = harness.service.last_report()
        assert report is not None and report.ok
        assert codes.DV_MANUAL_MAPPING in _codes(harness)
        assert harness.runner.calls == []  # no tool ran
        html = _html(harness)
        assert "FAKE: map Swc1_Cyclic10ms to Task_10ms" in html  # the runner's own text
        assert "TMT_Swc1_Cyclic10ms_20ms" in html and "Map them by hand" in html
    finally:
        harness.service.close()


def test_manual_instructions_fall_back_to_the_built_in_text_without_a_runner(
    repo: Repo, tmp_path: Path
) -> None:
    harness = make_service(repo, tmp_path)

    def no_runner(_config: ProjectConfig) -> DaVinciRunner:
        raise PipelineError("porttool.davinci is not installed")

    harness.service._runner_factory = no_runner
    try:
        harness.dry_run(TRIGGER_ROWS)
        harness.apply()
        html = _html(harness)
        assert "Open the project (.dpa) in DaVinci Configurator." in html
        assert "Map event TMT_Swc1_Cyclic10ms_20ms of runnable Swc1_Cyclic10ms" in html
        assert "to task Task_10ms, after event" in html
    finally:
        harness.service.close()


def test_a_failed_check_fails_the_apply_and_rolls_back(tmp_path: Path) -> None:
    repo = _repo(tmp_path, '\n[pipeline]\ndavinci_steps = ["check"]\n')
    failed = tool_result("DVWspChecker", ok=False, findings=(error_finding("DV-9", "bad XML"),))
    runner = FakeRunner(results={"check": [failed]})
    harness = make_service(repo, tmp_path, runner=runner)
    try:
        harness.dry_run(ROWS)
        before = repo.snapshot()
        view = harness.apply()
        report = harness.service.last_report()
        assert view.state == "done" and report is not None
        assert not report.ok and report.rolled_back
        assert {"DV-9", codes.DV_CHECK_FAILED, codes.RB_DONE} <= set(_codes(harness))
        assert runner.calls == ["check"]  # no fallback: it is off by default
        assert repo.snapshot() == before
    finally:
        harness.service.close()


def _fallback_harness(tmp_path: Path, results: dict[str, list[object]]) -> Harness:
    repo = _repo(tmp_path, '\n[pipeline]\ndavinci_steps = ["check"]\nfallback_import = true\n')
    runner = FakeRunner(results=results)  # type: ignore[arg-type]
    harness = make_service(repo, tmp_path, runner=runner)
    harness.dry_run(ROWS)
    plan, model, config = (
        harness.service.last_plan(),
        harness.service.model(),
        harness.service.config(),
    )
    assert plan is not None and model is not None and config is not None

    def fake_dvimex(delta: Path) -> None:
        # The fake import tool writes the workspace itself; the pipeline journals none
        # of these files by name, only the folders the tools may write.
        assert delta.is_file() and not delta.is_relative_to(repo.root)
        assert "PpCrash" not in (repo.root / SWC1).read_text("utf-8")  # direct edit was undone
        harness.writer.write_files(plan, model, config, None, None)

    runner.on_import = fake_dvimex
    return harness


def test_fallback_import_succeeds(tmp_path: Path) -> None:
    harness = _fallback_harness(tmp_path, {"check": [tool_result("DVWspChecker", ok=False)]})
    try:
        view = harness.apply()
        report = harness.service.last_report()
        assert view.state == "done" and report is not None and report.ok, report
        assert harness.runner.calls == ["check", "import", "check"]
        assert codes.DV_FALLBACK_USED in _codes(harness)
        assert len(harness.writer.deltas) == 2  # one for the import, one for the model owner
        model = harness.service.model()
        assert model is not None and "/ComponentTypes/Swc1/PpCrash" in model.ports
        committed = harness.repo.git("show", "--name-only", "--format=", "HEAD").split()
        assert SWC1 in committed and "porttool/pending.json" in committed
    finally:
        harness.service.close()


@pytest.mark.parametrize("failing", ["import", "second check"])
def test_fallback_also_fails_and_everything_is_rolled_back(tmp_path: Path, failing: str) -> None:
    bad = tool_result("DVWspChecker", ok=False)
    results: dict[str, list[object]] = {"check": [bad, bad]}
    if failing == "import":
        results = {"check": [bad], "import": [tool_result("DVImEx", ok=False)]}
    harness = _fallback_harness(tmp_path, results)
    try:
        before = harness.repo.snapshot()
        view = harness.apply()
        report = harness.service.last_report()
        assert view.state == "done" and report is not None
        assert not report.ok and report.rolled_back
        assert codes.DV_FALLBACK_FAILED in _codes(harness)
        expected = ["check", "import"] if failing == "import" else ["check", "import", "check"]
        assert harness.runner.calls == expected
        # Also the files the import tool wrote (journaled only as a folder) are restored.
        assert harness.repo.snapshot() == before
    finally:
        harness.service.close()


@pytest.mark.parametrize(
    ("step", "code"),
    [
        ("script", codes.DV_SCRIPT_FAILED),
        ("validate", codes.DV_VALIDATE_FAILED),
        ("generate", codes.DV_GENERATE_FAILED),
    ],
)
def test_a_failed_enabled_step_fails_the_apply(tmp_path: Path, step: str, code: str) -> None:
    repo = _repo(tmp_path, ALL_STEPS)
    runner = FakeRunner(
        results={step: [tool_result("DVCfgCmd", ok=False)]},
        on_call=lambda name: tool_writes(repo.root, name),
    )
    harness = make_service(repo, tmp_path, runner=runner)
    try:
        harness.dry_run(TRIGGER_ROWS)
        before = repo.snapshot()
        view = harness.apply()
        report = harness.service.last_report()
        assert view.state == "done" and report is not None
        assert not report.ok and report.rolled_back and code in _codes(harness)
        assert runner.calls[-1] == step
        assert repo.snapshot() == before  # ECUC edit and generated files included
        if step == "script":
            assert "FAKE: map Swc1_Cyclic10ms to Task_10ms" in _html(harness)
    finally:
        harness.service.close()


@pytest.mark.parametrize("step", ["check", "script", "validate", "generate"])
@pytest.mark.parametrize("how", ["failure", "cancel"])
def test_failure_or_cancel_at_each_davinci_step_restores_the_repo(
    tmp_path: Path, step: str, how: str
) -> None:
    repo = _repo(tmp_path, ALL_STEPS)
    holder: list[Harness] = []

    def on_call(name: str) -> None:
        tool_writes(repo.root, name)
        if name != step:
            return
        if how == "failure":
            raise OSError("the tool could not be started")
        service = holder[0].service
        service.cancel(next(job for job in service.jobs() if job.name == "apply").id)

    harness = make_service(repo, tmp_path, runner=FakeRunner(on_call=on_call))
    holder.append(harness)
    try:
        harness.dry_run(TRIGGER_ROWS)
        before = repo.snapshot()
        view = harness.apply()
        report = harness.service.last_report()
        assert view.state == ("failed" if how == "failure" else "cancelled"), view
        assert report is not None and not report.ok and report.rolled_back
        assert repo.snapshot() == before
        assert harness.service.recovery_needed() is False
    finally:
        harness.service.close()


def test_kill_during_the_davinci_steps_is_recovered_from_the_journal_scopes(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path, ALL_STEPS)
    before = repo.snapshot()
    _kill_child(repo, tmp_path, "generate", "end")
    assert (repo.root / "Appl/GenData/Rte_New.h").exists()
    harness = make_service(repo, tmp_path)
    try:
        assert harness.service.recovery_needed() is True
        view = harness.run(harness.service.start_rollback())
        assert view.summary is not None and view.summary["ok"] is True
        assert repo.snapshot() == before
    finally:
        harness.service.close()


def test_tracked_generated_files_are_committed_and_listed(tmp_path: Path) -> None:
    repo = _repo(tmp_path, '\n[pipeline]\ndavinci_steps = ["generate"]\n')
    runner = FakeRunner(on_call=lambda name: tool_writes(repo.root, name))
    harness = make_service(repo, tmp_path, runner=runner)
    try:
        harness.dry_run(ROWS)
        harness.apply()
        report = harness.service.last_report()
        assert report is not None and report.ok, report
        committed = repo.git("show", "--name-only", "--format=", "HEAD").split()
        assert {"Appl/GenData/Rte_New.h", "Appl/GenData/Rte_Type.h"} <= set(committed)
        html = _html(harness)
        assert "<li>Appl/GenData/Rte_New.h</li>" in html
        assert "<li>Appl/GenData/Rte_Type.h</li>" in html
        assert view_skipped(harness) == ["check", "script", "validate"]
    finally:
        harness.service.close()


def view_skipped(harness: Harness) -> object:
    view = harness.service.jobs()[-1]
    assert view.summary is not None
    return view.summary["skipped_steps"]


def test_generated_folder_ignored_by_git(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path, '\n[pipeline]\ndavinci_steps = ["generate"]\n', gitignore="Appl/GenData/\n"
    )
    assert "Rte_Type.h" not in repo.git("ls-files")  # ignored from the start
    runner = FakeRunner(on_call=lambda name: tool_writes(repo.root, name))
    harness = make_service(repo, tmp_path, runner=runner)
    try:
        # Ignored generated files never make the tree dirty.
        (repo.root / "Appl/GenData/Rte_Old.h").write_text("/* stale */\n", encoding="utf-8")
        harness.dry_run(ROWS)
        harness.apply()
        report = harness.service.last_report()
        assert report is not None and report.ok, report
        assert codes.DV_GENERATED_IGNORED in _codes(harness)
        assert codes.PRE_UNTRACKED_OTHER not in _codes(harness)
        committed = repo.git("show", "--name-only", "--format=", "HEAD").split()
        assert not [path for path in committed if path.startswith("Appl/")]
        # The report still lists what generation changed (found by comparing the folder).
        html = _html(harness)
        assert "<li>Appl/GenData/Rte_New.h</li>" in html
        assert "<li>Appl/GenData/Rte_Type.h</li>" in html
        assert "Rte_Old.h" not in html
    finally:
        harness.service.close()


def test_rollback_says_that_an_ignored_generated_folder_cannot_be_restored(
    tmp_path: Path,
) -> None:
    repo = _repo(
        tmp_path, '\n[pipeline]\ndavinci_steps = ["generate"]\n', gitignore="Appl/GenData/\n"
    )

    def hook(name: str, moment: str) -> None:
        if (name, moment) == ("verify", "start"):
            raise RuntimeError("injected")

    runner = FakeRunner(on_call=lambda name: tool_writes(repo.root, name))
    harness = make_service(repo, tmp_path, runner=runner, step_hook=hook)
    try:
        harness.dry_run(ROWS)
        before = repo.snapshot()
        harness.apply()
        report = harness.service.last_report()
        assert report is not None and report.rolled_back
        assert codes.RB_SCOPE_IGNORED in _codes(harness)
        after = repo.snapshot()
        files, old = after["files"], before["files"]
        assert isinstance(files, dict) and isinstance(old, dict)
        changed = {path for path in files.keys() | old.keys() if files.get(path) != old.get(path)}
        assert changed == {"Appl/GenData/Rte_New.h", "Appl/GenData/Rte_Type.h"}
        assert after["head"] == before["head"] and after["status"] == before["status"]
    finally:
        harness.service.close()


def test_open_davinci_gui_refuses_the_apply_when_a_step_is_enabled(tmp_path: Path) -> None:
    repo = _repo(tmp_path, '\n[pipeline]\ndavinci_steps = ["check"]\n')
    open_gui = Finding("DV-GUI-001", Severity.WARNING, "DaVinci Developer has the workspace open")
    harness = make_service(repo, tmp_path, gui_findings=lambda _config: (open_gui,))
    try:
        harness.dry_run(ROWS)
        before = repo.snapshot()
        view = harness.apply()
        assert view.summary is not None and view.summary["ok"] is False
        assert {"DV-GUI-001", codes.PRE_GUI} <= set(_codes(harness))
        assert harness.runner.calls == [] and repo.snapshot() == before
    finally:
        harness.service.close()


def test_open_davinci_gui_does_not_matter_when_no_step_is_enabled(
    repo: Repo, tmp_path: Path
) -> None:
    def gui(_config: ProjectConfig) -> tuple[Finding, ...]:
        raise AssertionError("must not be asked when no DaVinci step is enabled")

    harness = make_service(repo, tmp_path, gui_findings=gui)
    try:
        harness.dry_run(ROWS)
        harness.apply()
        report = harness.service.last_report()
        assert report is not None and report.ok
    finally:
        harness.service.close()


def test_missing_runner_refuses_the_apply_when_a_step_is_enabled(tmp_path: Path) -> None:
    repo = _repo(tmp_path, '\n[pipeline]\ndavinci_steps = ["check"]\n')
    harness = make_service(repo, tmp_path, writer=FakeWriter())

    def no_runner(_config: ProjectConfig) -> DaVinciRunner:
        raise PipelineError("porttool.davinci is not installed")

    harness.service._runner_factory = no_runner
    try:
        harness.dry_run(ROWS)
        view = harness.apply()
        assert view.summary is not None and view.summary["ok"] is False
        assert codes.PRE_RUNNER in _codes(harness)
        assert harness.repo.branch() == "main"
    finally:
        harness.service.close()


def test_doctor_reports_git_settings_and_runner(tmp_path: Path) -> None:
    repo = _repo(tmp_path, '\n[pipeline]\ndavinci_steps = ["check"]\nnonsense = 1\n')
    note = Finding("DV-DOC-1", Severity.WARNING, "DVCfgCmd command line is UNVERIFIED")
    harness = make_service(repo, tmp_path, runner=FakeRunner(doctor_findings=(note,)))
    try:
        view = harness.run(harness.service.start_doctor())
        assert view.state == "done" and view.summary is not None and view.summary["ok"] is True
        items = view.summary["items"]
        assert isinstance(items, list)
        found = {item["code"] for item in items}
        assert {"DV-DOC-1", codes.CFG_UNKNOWN_KEY, codes.DOC_OK} <= found
        assert any("git found" in item["message"] for item in items)
        assert any("enabled in [pipeline] davinci_steps: check" in i["message"] for i in items)
    finally:
        harness.service.close()


def test_arxml_root_that_is_the_repository_folder_is_journaled_as_one_scope(
    tmp_path: Path,
) -> None:
    # The whole repository is the ARXML root: the tool scope is "." and still restores.
    toml = ALL_STEPS.replace("[pipeline]", '[pipeline]\ntool_write_scopes = ["."]')
    repo = _repo(tmp_path, toml)
    runner = FakeRunner(
        results={"validate": [tool_result("DVCfgCmd", ok=False)]},
        on_call=lambda name: tool_writes(repo.root, name),
    )
    harness = make_service(repo, tmp_path, runner=runner)
    try:
        harness.dry_run(TRIGGER_ROWS)
        before = repo.snapshot()
        harness.apply()
        report = harness.service.last_report()
        assert report is not None and not report.ok and report.rolled_back
        assert codes.DV_VALIDATE_FAILED in _codes(harness)
        assert repo.snapshot() == before
    finally:
        harness.service.close()
