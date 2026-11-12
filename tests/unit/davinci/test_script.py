"""Script request and result files, the manual fallback, and the shipped Groovy file."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

from porttool.core.config import ProjectConfig
from porttool.core.results import Severity
from porttool.davinci import (
    DaVinciToolRunner,
    ScriptEvent,
    manual_mapping_instructions,
    read_script_request,
    read_script_result,
    write_script_request,
)
from porttool.davinci.doctor import doctor_findings
from porttool.davinci.procrun import judge_line
from porttool.davinci.settings import DEFAULT_SCRIPT_DIR, SCRIPT_FILE_NAME, load_settings
from tests.unit.davinci.helpers import FakeCtx, codes, fake_commands, make_config, run

EV_A = ScriptEvent(
    swc="SwcA",
    runnable="RunA_OnSpeed",
    event_path="/ComponentTypes/SwcA/SwcA_IB/DRE_RunA_OnSpeed",
    task="Task_10ms",
    position_after_event="/ComponentTypes/SwcA/SwcA_IB/TE_RunA_10ms",
)
EV_B = ScriptEvent("SwcB", "RunB_Init", "/ComponentTypes/SwcB/SwcB_IB/IE_RunB_Init", "Task_Init", 0)
EV_C = ScriptEvent("SwcC", "RunC", "/ComponentTypes/SwcC/SwcC_IB/TE_RunC", "Task_Größe")
RULES = load_settings(ProjectConfig(repo_root=Path(".")))[0].results


def _result(tmp_path: Path, **changes: Any) -> Path:
    """A complete, good result for EV_A and EV_B, changed as asked."""
    document: dict[str, Any] = {
        "format": "porttool-script-result",
        "format_version": 1,
        "complete": True,
        "saved": True,
        "steps": [{"name": "read-request", "ok": True, "message": ""}],
        "events": [
            {"event_path": EV_A.event_path, "task": "Task_10ms", "status": "mapped"},
            {"event_path": EV_B.event_path, "task": "Task_Init", "status": "already_mapped"},
        ],
        "solving_actions": {"attempted": 1, "applied": 1, "failed": 0, "remaining_errors": 0},
    }
    document.update(changes)
    path = tmp_path / "result.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


# ------------------------------------------------------------------ request


def test_request_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "request.json"
    write_script_request([EV_A, EV_B, EV_C], path)
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["format"] == "porttool-script-request" and document["format_version"] == 1
    assert document["options"] == {
        "apply_solving_actions": True,
        "solving_action_origins": ["RTE", "OS"],
        "save_project": True,
    }
    assert document["events"][0] == {
        "swc": "SwcA",
        "runnable": "RunA_OnSpeed",
        "event_path": EV_A.event_path,
        "task": "Task_10ms",
        "position": None,
        "position_after_event": EV_A.position_after_event,
    }
    assert "Task_Größe" in path.read_text(encoding="utf-8")  # written as UTF-8, not escaped
    assert read_script_request(path) == ((EV_A, EV_B, EV_C), ())
    assert not list(path.parent.glob("*.tmp*"))


def test_request_options_can_switch_solving_and_saving_off(tmp_path: Path) -> None:
    path = tmp_path / "request.json"
    write_script_request([], path, apply_solving_actions=False, save_project=False)
    options = json.loads(path.read_text(encoding="utf-8"))["options"]
    assert options["apply_solving_actions"] is False and options["save_project"] is False
    assert read_script_request(path) == ((), ())


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (None, "DVR-SCRIPT-REQUEST-MISSING"),
        ("{ not json", "DVR-SCRIPT-REQUEST-MALFORMED"),
        ("[1, 2]", "DVR-SCRIPT-REQUEST-MALFORMED"),
        ('{"format": "something else", "events": []}', "DVR-SCRIPT-REQUEST-MALFORMED"),
        (
            '{"format": "porttool-script-request", "events": [{"task": "T"}]}',
            "DVR-SCRIPT-REQUEST-MALFORMED",
        ),
    ],
)
def test_bad_request_files_are_findings(tmp_path: Path, content: str | None, code: str) -> None:
    path = tmp_path / "request.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    events, findings = read_script_request(path)
    assert events == () and codes(findings) == [code]
    assert findings[0].severity is Severity.ERROR and findings[0].file == str(path)


# ------------------------------------------------------------------ result


def test_good_result_confirms_every_event(tmp_path: Path) -> None:
    result = read_script_result(_result(tmp_path), [EV_A, EV_B])
    assert result.ok and result.complete and result.saved
    assert [outcome.status for outcome in result.events] == ["mapped", "already_mapped"]
    assert result.unconfirmed == ()
    assert codes(result.findings) == ["DVR-SCRIPT-OK"]
    assert "confirmed 2 of 2 event(s)" in result.findings[0].message


def test_script_result_missing(tmp_path: Path) -> None:
    result = read_script_result(tmp_path / "result.json", [EV_A])
    assert not result.ok and not result.complete
    assert codes(result.findings) == ["DVR-SCRIPT-RESULT-MISSING"]
    assert "did not run to the point where it writes its result" in result.findings[0].message
    assert result.unconfirmed == (EV_A,)


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b'{"format": "porttool-script-result", "events": [',
        b"\xff\xfe\x00not text",
        b'"just a text"',
        b'{"format": "porttool-script-result", "events": "none"}',
        b'{"format": "other", "events": []}',
    ],
    ids=["empty", "cut-off", "not-utf8", "not-an-object", "events-not-a-list", "other-format"],
)
def test_script_result_malformed(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "result.json"
    path.write_bytes(content)
    result = read_script_result(path, [EV_A, EV_B])
    assert not result.ok
    assert codes(result.findings) == ["DVR-SCRIPT-RESULT-MALFORMED"]
    assert result.unconfirmed == (EV_A, EV_B)


def test_script_result_partial(tmp_path: Path) -> None:
    path = _result(
        tmp_path,
        complete=False,
        saved=False,
        steps=[
            {"name": "read-request", "ok": True, "message": ""},
            {"name": "map-events", "ok": False, "message": "java.lang.NullPointerException: x"},
        ],
        events=[{"event_path": EV_A.event_path, "task": "Task_10ms", "status": "mapped"}],
    )
    result = read_script_result(path, [EV_A, EV_B])
    assert not result.ok and not result.complete
    assert codes(result.findings) == [
        "DVR-SCRIPT-RESULT-PARTIAL",
        "DVR-SCRIPT-STEP-FAILED",
        "DVR-SCRIPT-EVENT-MISSING",
    ]
    assert "The last step it reached was 'map-events'" in result.findings[0].message
    assert "NullPointerException" in result.findings[1].message
    assert result.unconfirmed == (EV_B,)  # EV_A was mapped before the script stopped


def test_trigger_whose_task_is_not_in_the_result(tmp_path: Path) -> None:
    result = read_script_result(_result(tmp_path), [EV_A, EV_B, EV_C])
    assert not result.ok
    (finding,) = [f for f in result.findings if f.severity is Severity.ERROR]
    assert finding.code == "DVR-SCRIPT-EVENT-MISSING"
    assert finding.object_path == EV_C.event_path
    assert "SWC SwcC, runnable RunC" in finding.message and "Task_Größe" in finding.message
    assert result.unconfirmed == (EV_C,)
    assert manual_mapping_instructions(result.unconfirmed)[4].startswith("   3.1 SWC SwcC")


def test_event_that_failed_or_went_to_another_task(tmp_path: Path) -> None:
    path = _result(
        tmp_path,
        events=[
            {
                "event_path": EV_A.event_path,
                "task": "Task_10ms",
                "status": "failed",
                "message": "the OS has no task named Task_10ms",
            },
            {"event_path": EV_B.event_path, "task": "Task_Other", "status": "already_mapped"},
            {"task": "no path"},
            "not an object",
        ],
    )
    result = read_script_result(path, [EV_A, EV_B])
    assert not result.ok
    assert codes(result.findings) == [
        "DVR-SCRIPT-RESULT-MALFORMED",
        "DVR-SCRIPT-RESULT-MALFORMED",
        "DVR-SCRIPT-EVENT-FAILED",
        "DVR-SCRIPT-TASK-MISMATCH",
    ]
    assert "the OS has no task named Task_10ms" in result.findings[2].message
    assert "mapped to the task Task_Other" in result.findings[3].message
    assert result.unconfirmed == (EV_A, EV_B)


def test_mapped_but_not_saved_and_remaining_validation_errors(tmp_path: Path) -> None:
    path = _result(
        tmp_path,
        saved=False,
        solving_actions={
            "attempted": 3,
            "applied": 1,
            "failed": 2,
            "remaining_errors": 4,
            "messages": ["remaining: RTE01039 event not mapped"],
        },
    )
    result = read_script_result(path, [EV_A, EV_B])
    assert not result.ok
    assert codes(result.findings) == ["DVR-SCRIPT-NOT-SAVED", "DVR-SCRIPT-VALIDATION"]
    validation = result.findings[1]
    assert validation.severity is Severity.WARNING
    assert "4 error(s); 2 solving action(s) failed" in validation.message
    assert "RTE01039" in validation.message


def test_result_without_expected_events_is_judged_on_its_own(tmp_path: Path) -> None:
    result = read_script_result(_result(tmp_path, steps="none", solving_actions=None))
    assert result.ok and result.findings == () and len(result.events) == 2


# ------------------------------------------------------------------ manual instructions


def test_manual_mapping_instructions_name_every_event() -> None:
    lines = manual_mapping_instructions([EV_A, EV_B, EV_C])
    text = "\n".join(lines)
    assert lines[0].startswith("Map 3 new RTE event(s)")
    assert [line[:2] for line in lines[1:4]] == ["1.", "2.", "3."]
    assert (
        f"3.1 SWC SwcA, runnable RunA_OnSpeed, event {EV_A.event_path}: map it to the task "
        f"Task_10ms, directly after the event {EV_A.position_after_event}." in text
    )
    assert "3.2 SWC SwcB" in text and "map it to the task Task_Init, at position 0." in text
    assert "3.3 SWC SwcC" in text and "after the last event of that task." in text
    assert "preferred solving action" in text
    assert lines[-2].startswith("5. Save the project")
    assert lines[-1].startswith("6. Run PortTool again")
    assert manual_mapping_instructions([]) == ["No event has to be mapped to a task."]


# ------------------------------------------------------------------ through the adapter

_WRITER = (
    "import json, os; "
    "request = json.load(open(os.environ['PORTTOOL_REQUEST_JSON'], encoding='utf-8')); "
    "events = [dict(event_path=e['event_path'], task=e['task'], status='mapped') "
    "for e in request['events']][:int(os.environ.get('KEEP', '99'))]; "
    "json.dump(dict(format='porttool-script-result', format_version=1, complete=True, "
    "saved=True, steps=[], events=events), "
    "open(os.environ['PORTTOOL_RESULT_JSON'], 'w', encoding='utf-8'))"
)


def _script_runner(tmp_path: Path, code: str, **env: str) -> DaVinciToolRunner:
    commands = {**fake_commands(), "script_task": [sys.executable, "-c", code, "{dpa}"]}
    return DaVinciToolRunner(make_config(tmp_path, commands=commands, env=env))


def test_script_task_result_is_part_of_the_tool_result(tmp_path: Path) -> None:
    request, result_file = tmp_path / "request.json", tmp_path / "out" / "result.json"
    write_script_request([EV_A, EV_B], request)
    runner = _script_runner(tmp_path, _WRITER)
    result = run(runner.run_script_task(request, result_file, FakeCtx()))
    assert result.ok and result.exit_code == 0 and result.tool == "DVCfgCmd"
    assert codes(result.findings) == ["DVR-SCRIPT-OK"]


def test_script_task_that_drops_an_event_is_not_ok(tmp_path: Path) -> None:
    request, result_file = tmp_path / "request.json", tmp_path / "result.json"
    write_script_request([EV_A, EV_B], request)
    runner = _script_runner(tmp_path, _WRITER, KEEP="1")
    result = run(runner.run_script_task(request, result_file, FakeCtx()))
    assert not result.ok and result.exit_code == 0
    assert codes(result.findings) == ["DVR-SCRIPT-EVENT-MISSING"]
    assert result.findings[0].object_path == EV_B.event_path


def test_script_task_that_writes_no_result_never_sees_an_old_one(tmp_path: Path) -> None:
    request, result_file = tmp_path / "request.json", tmp_path / "result.json"
    write_script_request([EV_A], request)
    stale = _result(tmp_path)  # a good result left over from an earlier run
    assert stale == result_file
    runner = _script_runner(tmp_path, "print('the script did nothing')")
    result = run(runner.run_script_task(request, result_file, FakeCtx()))
    assert not result.ok and result.exit_code == 0
    assert codes(result.findings) == ["DVR-SCRIPT-RESULT-MISSING"]
    assert not result_file.exists()


def test_script_task_with_a_bad_request_starts_nothing(tmp_path: Path) -> None:
    runner = _script_runner(tmp_path, "raise SystemExit(9)")
    result = run(runner.run_script_task(tmp_path / "none.json", tmp_path / "r.json", FakeCtx()))
    assert not result.ok and result.exit_code is None
    assert codes(result.findings) == ["DVR-SCRIPT-REQUEST-MISSING"]


def test_script_task_that_cannot_start_reads_no_result(tmp_path: Path) -> None:
    request = tmp_path / "request.json"
    write_script_request([EV_A], request)
    commands = {**fake_commands(), "script_task": "not a list"}
    runner = DaVinciToolRunner(make_config(tmp_path, commands=commands))
    result = run(runner.run_script_task(request, tmp_path / "r.json", FakeCtx()))
    assert result.exit_code is None and codes(result.findings) == ["DVR-TPL-NOT-LIST"]


def test_old_result_that_cannot_be_removed_starts_nothing(tmp_path: Path) -> None:
    request = tmp_path / "request.json"
    write_script_request([EV_A], request)
    result_file = tmp_path / "result.json"
    (result_file / "inside").mkdir(parents=True)  # a folder: unlink fails
    runner = _script_runner(tmp_path, "raise SystemExit(9)")
    result = run(runner.run_script_task(request, result_file, FakeCtx()))
    assert result.exit_code is None and codes(result.findings) == ["DVR-SCRIPT-RESULT-STALE"]


# ------------------------------------------------------------------ the shipped Groovy file

GROOVY = DEFAULT_SCRIPT_DIR / SCRIPT_FILE_NAME


def test_groovy_file_is_marked_experimental_and_lists_the_calls_to_check() -> None:
    text = GROOVY.read_text(encoding="utf-8")
    head = "\n".join(text.splitlines()[:12])
    assert "EXPERIMENTAL / UNVERIFIED" in head and "NEVER been run" in head
    assert "API CALLS THAT MUST BE CHECKED" in text
    listed = set(re.findall(r"^//\s+\[(A\d+)\]", text, flags=re.MULTILINE))
    assert listed == {f"A{number}" for number in range(1, 15)}
    body = text.split("daVinci {", 1)[1]
    for tag in listed:
        assert f"[{tag}]" in body, f"{tag} is listed but marks no code"
    assert 'scriptTask("PortToolMapEvents", DV_PROJECT)' in text
    assert 'scriptTask("PortToolValidate", DV_PROJECT)' in text
    assert (
        (DEFAULT_SCRIPT_DIR / "README.md")
        .read_text(encoding="utf-8")
        .startswith("# PortToolTasks.dvgroovy: EXPERIMENTAL / UNVERIFIED")
    )


def test_groovy_file_and_python_agree_on_the_formats() -> None:
    text = GROOVY.read_text(encoding="utf-8")
    for word in (
        '"porttool-script-result"',
        "PORTTOOL_REQUEST_JSON",
        "PORTTOOL_RESULT_JSON",
        '"already_mapped"',
        '"mapped"',
        '"failed"',
        "complete: false",
        "result.complete = true",
        "result.saved = true",
        "remaining_errors",
        "position_after_event",
        "apply_solving_actions",
        "save_project",
    ):
        assert word in text, word
    assert text.count("{") == text.count("}") and text.count("(") == text.count(")")


def test_groovy_info_lines_are_not_counted_as_errors_or_warnings() -> None:
    text = GROOVY.read_text(encoding="utf-8")
    steps = re.findall(r'step\("([^"]+)"\)', text)
    assert "map-events" in steps and "save-project" in steps
    infos = re.findall(r'logInfo\("([^"]*)"', text)
    assert len(infos) >= 6
    for literal in [f"PortTool: step {name}: start" for name in steps] + infos:
        # A Groovy expression such as ${errors.size()} is tried with the value 0.
        line = re.sub(r"\$\{[^}]*\}", "0", literal)
        assert judge_line(RULES, line) is None, literal
    assert (
        judge_line(RULES, "PortTool: validation finished: 0 errors, 12 messages in total") is None
    )
    assert judge_line(RULES, "PortTool: validation finished: 3 errors, 12 messages") == "error"


def test_doctor_flags_the_shipped_groovy_script_as_unverified(tmp_path: Path) -> None:
    runner = DaVinciToolRunner(ProjectConfig(repo_root=tmp_path), log_dir=tmp_path / "logs")
    findings = doctor_findings(runner, processes=lambda: [])
    (finding,) = [f for f in findings if f.code == "DVR-SCRIPT-UNVERIFIED"]
    assert finding.severity is Severity.WARNING
    assert "EXPERIMENTAL and UNVERIFIED" in finding.message and finding.file == str(GROOVY)
