"""The HTML report: one self-contained file, everything escaped."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

from porttool.core.models import PortDirection
from porttool.core.operations import AddPort, AddServerRunnable
from porttool.core.results import Finding, RequestResult, RequestStatus, RowRef, Severity
from porttool.pipeline.packages import Package
from porttool.pipeline.report import ReportData, StepTiming, render_report
from tests.unit.pipeline.support import Harness, port_row

EVIL = '<script>alert("x")</script> & <img src=http://evil.example/x.png>'


def _data(**changes: object) -> ReportData:
    port = AddPort(
        id="Ports:add_port:/C/Swc1/PpNew",
        swc_path="/C/Swc1",
        name="PpNew",
        direction=PortDirection.P,
        interface_path="/I/IfSrv",
        requester=EVIL,
    )
    runnable = AddServerRunnable(
        id="Ports:add_server_runnable:/C/Swc1/PpNew_Op1",
        swc_path="/C/Swc1",
        name="PpNew_Op1",
        symbol="PpNew_Op1",
        port_name="PpNew",
        operation="Op1",
    )
    base = ReportData(
        ok=True,
        rolled_back=False,
        repo_root=r"C:\Projekte\ECU <1>",
        created="2026-11-12 10:00:00",
        tool_version="0.0.1",
        original_branch="main",
        original_commit="a" * 40,
        branch="porttool/20261112-1",
        commit="b" * 40,
        results=(
            RequestResult(
                request_id="r1",
                status=RequestStatus.APPLIED,
                row=RowRef(file=f"C:\\x\\{EVIL}.xlsx", sheet="Ports", row=14),
                operation_ids=(port.id, runnable.id),
            ),
            RequestResult(
                request_id="r2",
                status=RequestStatus.REJECTED,
                row=RowRef(file="requests.xlsx", sheet="Ports", row=15),
                findings=(Finding("VAL-NAME-001", Severity.ERROR, f"bad name {EVIL}"),),
            ),
        ),
        operations=(port, runnable),
        applied_ids=frozenset({port.id, runnable.id}),
        findings=(
            Finding(
                "PIP-APPLY-007", Severity.WARNING, "DaVinci steps skipped", file="porttool.toml"
            ),
            Finding("X-1", Severity.INFO, EVIL, object_path="/C/Swc1"),
        ),
        timings=(
            StepTiming("write", "ok", 0.42),
            StepTiming("check", "skipped", 0.0, "not enabled"),
            StepTiming("verify", "failed", 1.5),
        ),
        skipped_steps=("check", "script"),
        enable_hint="set davinci_steps",
        trigger_mappings=(
            {
                "swc_path": "/C/Swc1",
                "runnable": "Run10ms",
                "event_name": "TMT_Run10ms",
                "trigger_kind": "periodic",
                "task": "Task_10ms",
                "position_after_event": "/C/Swc1/IB/TMT_Other",
            },
        ),
        manual_instructions=("Open the project", f"Map {EVIL}"),
        tool_logs=(("check: DVWspChecker (ok)", f"line 1\n{EVIL}\nline 3"),),
        diff_stat=" Config/Swc1.arxml | 5 +++++\n 1 file changed, 5 insertions(+)",
        changed_files=("Config/Swc1.arxml", "porttool/pending.json"),
        generated_files=("Appl/GenData/Rte_Swc1.h",),
        packages=(Package("Team <M>", r"C:\reports\model_requests\Team_M", ("requests.csv",), 2),),
    )
    from dataclasses import replace

    return replace(base, **changes)  # type: ignore[arg-type]


class _Checker(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []
        self.external: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        for name, value in attrs:
            if name in {"src", "href"} or (value and "://" in value):
                self.external.append(f"{tag} {name}={value}")


def test_report_is_self_contained_and_escapes_everything() -> None:
    html = render_report(_data())
    assert html.startswith("<!DOCTYPE html>")
    checker = _Checker()
    checker.feed(html)
    # No script, no image, no stylesheet link, no frame, no attribute that loads anything.
    assert not {"script", "img", "link", "iframe", "object", "embed"} & set(checker.tags)
    assert checker.external == []
    assert "<script>" not in html and "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in html
    assert "ECU &lt;1&gt;" in html and "Team &lt;M&gt;" in html
    assert not re.search(r"@import|url\(", html)


def test_report_has_every_required_section() -> None:
    html = render_report(_data())
    for heading in (
        "Summary",
        "Requests",
        "Created runnables",
        "Triggers that need a task mapping",
        "Findings",
        "Step timings",
        "Tool log excerpts",
        "git diff --stat",
        "Changed files",
        "Changed generated files",
        "Request packages for model owners",
    ):
        assert f"<h2>{heading}</h2>" in html, heading
    assert "APPLIED" in html and "porttool/20261112-1" in html and "b" * 40 in html
    assert "1 applied, 1 rejected" in html
    assert "row 14" in html and "VAL-NAME-001" in html  # status and reasons per request
    assert "add port /C/Swc1/PpNew (P, /I/IfSrv)" in html
    assert "<td>PpNew_Op1</td>" in html  # created runnable
    assert "Task_10ms" in html and "TMT_Run10ms" in html and "Map them by hand" in html
    assert "<li>Open the project</li>" in html
    assert "DaVinci steps skipped" in html and "set davinci_steps" in html
    assert "0.42" in html and "not enabled" in html
    assert "check: DVWspChecker (ok)" in html and "line 3" in html
    assert "1 file changed, 5 insertions(+)" in html
    assert "<li>porttool/pending.json</li>" in html and "<li>Appl/GenData/Rte_Swc1.h</li>" in html


def test_failure_report_says_rolled_back_and_why() -> None:
    html = render_report(
        _data(ok=False, rolled_back=True, branch=None, commit=None, failure="Step 'verify' failed")
    )
    assert "FAILED - ROLLED BACK" in html and "Step &#x27;verify&#x27; failed" in html
    refused = render_report(_data(ok=False, rolled_back=False, branch=None, commit=None))
    assert "NOT APPLIED" in refused
    empty = render_report(
        _data(results=(), findings=(), tool_logs=(), trigger_mappings=(), manual_instructions=())
    )
    assert "No requests." in empty and "No DaVinci tool ran." in empty


def test_report_of_a_real_apply_names_commit_diff_and_skipped_steps(harness: Harness) -> None:
    harness.dry_run([port_row("Swc1", "PpNew", "P", "IfSig1")])
    harness.apply()
    report = harness.service.last_report()
    assert report is not None and report.report_file is not None
    html = Path(report.report_file).read_text(encoding="utf-8")
    assert harness.repo.head() in html  # rewritten with the commit hash after the commit
    assert "Swc1.arxml" in html and "1 file changed" in html
    assert "check, script, validate, generate" in html and "davinci_steps" in html
    assert "1 applied" in html and "write" in html and "verify" in html
    # Not committed: the report is untracked, and the report says how to ignore the folder.
    assert "porttool_reports" in harness.repo.status()
    assert "PIP-REP-001" in html
