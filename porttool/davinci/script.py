"""The request and result files of the Groovy script task, and the manual fallback.

PortTool writes a JSON request (which RTE event goes to which task), DVCfgCmd runs the
script task of ``groovy/PortToolTasks.dvgroovy``, and the script writes a JSON result.
The script is EXPERIMENTAL and UNVERIFIED, so this side trusts nothing: a result that is
missing, malformed or partial, and every event the result does not confirm, becomes a
Finding, and ``manual_mapping_instructions`` says how to do the same work by hand.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from porttool.core.fsutil import atomic_write_text
from porttool.core.results import Finding, Severity

REQUEST_FORMAT = "porttool-script-request"
RESULT_FORMAT = "porttool-script-result"
FORMAT_VERSION = 1
#: Statuses the script may give an event. Only the first two mean "mapped as asked".
STATUS_MAPPED = "mapped"
STATUS_ALREADY = "already_mapped"
STATUS_FAILED = "failed"
_GOOD_STATUSES = (STATUS_MAPPED, STATUS_ALREADY)
_BY_HAND = "Map it by hand: PortTool shows the steps."


@dataclass(frozen=True, slots=True)
class ScriptEvent:
    """One RTE event that has to be mapped to a task."""

    swc: str  # short name of the SWC
    runnable: str  # short name of the runnable the event starts
    event_path: str  # AUTOSAR path of the RTE event
    task: str  # short name of the OS task
    position: int | None = None  # position in the task; None: after the last one
    position_after_event: str | None = None  # path of the event this one follows

    def to_json(self) -> dict[str, object]:
        return {
            "swc": self.swc,
            "runnable": self.runnable,
            "event_path": self.event_path,
            "task": self.task,
            "position": self.position,
            "position_after_event": self.position_after_event,
        }


@dataclass(frozen=True, slots=True)
class EventOutcome:
    event_path: str
    task: str
    status: str
    message: str = ""


@dataclass(frozen=True, slots=True)
class ScriptResult:
    """What the script task reported, already judged."""

    ok: bool
    complete: bool
    saved: bool
    events: tuple[EventOutcome, ...] = ()
    findings: tuple[Finding, ...] = ()
    #: Requested events the result does not confirm: give them to
    #: ``manual_mapping_instructions``.
    unconfirmed: tuple[ScriptEvent, ...] = ()


def _finding(
    code: str,
    message: str,
    *,
    severity: Severity = Severity.ERROR,
    obj: str | None = None,
    file: str | os.PathLike[str] | None = None,
) -> Finding:
    return Finding(
        code, severity, message, object_path=obj, file=None if file is None else str(file)
    )


def write_script_request(
    events: Sequence[ScriptEvent],
    path: str | os.PathLike[str],
    *,
    apply_solving_actions: bool = True,
    save_project: bool = True,
) -> None:
    """Write the request file for the script task, atomically, as UTF-8 JSON."""
    document = {
        "format": REQUEST_FORMAT,
        "format_version": FORMAT_VERSION,
        "created_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "options": {
            "apply_solving_actions": apply_solving_actions,
            "solving_action_origins": ["RTE", "OS"],
            "save_project": save_project,
        },
        "events": [event.to_json() for event in events],
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(target, json.dumps(document, indent=2, ensure_ascii=False) + "\n")


def _load_object(path: Path, what: str, code: str) -> tuple[dict[str, object], Finding | None]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}, _finding(f"{code}-MISSING", f"The {what} {path} does not exist.", file=path)
    except OSError as error:
        problem = f"The {what} {path} cannot be read ({type(error).__name__}: {error})."
        return {}, _finding(f"{code}-MALFORMED", problem, file=path)
    try:
        document = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as error:
        problem = f"The {what} {path} is not valid JSON ({error})."
        return {}, _finding(f"{code}-MALFORMED", problem, file=path)
    if not isinstance(document, dict):
        problem = f"The {what} {path} does not hold a JSON object."
        return {}, _finding(f"{code}-MALFORMED", problem, file=path)
    return document, None


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _event_from(entry: object) -> ScriptEvent | None:
    if not isinstance(entry, Mapping):
        return None
    event_path, task = _text(entry.get("event_path")), _text(entry.get("task"))
    if not event_path or not task:
        return None
    position = entry.get("position")
    after = entry.get("position_after_event")
    return ScriptEvent(
        swc=_text(entry.get("swc")),
        runnable=_text(entry.get("runnable")),
        event_path=event_path,
        task=task,
        position=position if isinstance(position, int) and not isinstance(position, bool) else None,
        position_after_event=_text(after) or None,
    )


def read_script_request(
    path: str | os.PathLike[str],
) -> tuple[tuple[ScriptEvent, ...], tuple[Finding, ...]]:
    """Read a request file back. Problems are Findings (``DVR-SCRIPT-REQUEST-*``)."""
    file = Path(path)
    document, problem = _load_object(file, "script request file", "DVR-SCRIPT-REQUEST")
    if problem is not None:
        return (), (problem,)
    entries = document.get("events")
    if document.get("format") != REQUEST_FORMAT or not isinstance(entries, list):
        message = (
            f"The script request file {file} is not a PortTool script request "
            f'(expected "format": "{REQUEST_FORMAT}" and a list "events").'
        )
        return (), (_finding("DVR-SCRIPT-REQUEST-MALFORMED", message, file=file),)
    events = [_event_from(entry) for entry in entries]
    if any(event is None for event in events):
        message = (
            f"The script request file {file} holds an event without event_path or task."
        )
        return (), (_finding("DVR-SCRIPT-REQUEST-MALFORMED", message, file=file),)
    return tuple(event for event in events if event is not None), ()


def _step_findings(steps: object, file: Path) -> tuple[list[Finding], str]:
    """Findings for failed steps, and the name of the last step the script reached."""
    findings: list[Finding] = []
    last = ""
    if not isinstance(steps, list):
        return findings, last
    for step in steps:
        if not isinstance(step, Mapping):
            continue
        name = _text(step.get("name")) or "?"
        last = name
        if step.get("ok") is not True:
            reason = _text(step.get("message")) or "no reason was recorded"
            findings.append(
                _finding(
                    "DVR-SCRIPT-STEP-FAILED",
                    f"The script step '{name}' failed: {reason}",
                    obj=name,
                    file=file,
                )
            )
    return findings, last


def read_script_result(
    path: str | os.PathLike[str], expected: Sequence[ScriptEvent] = ()
) -> ScriptResult:
    """Read and judge the result file of the script task.

    ``expected`` are the events of the request. Each must be confirmed by the result as
    mapped to the requested task; otherwise it is reported and listed in ``unconfirmed``.
    A missing, malformed or partial result is reported as Findings, never raised.
    """
    file = Path(path)
    wanted = tuple(expected)
    document, problem = _load_object(file, "script result file", "DVR-SCRIPT-RESULT")
    if problem is not None:
        hint = (
            " The script task did not run to the point where it writes its result. Look "
            f"at the log of DVCfgCmd. {_BY_HAND}"
            if problem.code.endswith("MISSING")
            else f" {_BY_HAND}"
        )
        finding = _finding(problem.code, problem.message + hint, file=file)
        return ScriptResult(False, False, False, findings=(finding,), unconfirmed=wanted)
    entries = document.get("events")
    if document.get("format") != RESULT_FORMAT or not isinstance(entries, list):
        finding = _finding(
            "DVR-SCRIPT-RESULT-MALFORMED",
            f"The script result file {file} is not a PortTool script result (expected "
            f'"format": "{RESULT_FORMAT}" and a list "events"). {_BY_HAND}',
            file=file,
        )
        return ScriptResult(False, False, False, findings=(finding,), unconfirmed=wanted)

    findings, last_step = _step_findings(document.get("steps"), file)
    complete = document.get("complete") is True
    saved = document.get("saved") is True
    if not complete:
        where = f" The last step it reached was '{last_step}'." if last_step else ""
        findings.insert(
            0,
            _finding(
                "DVR-SCRIPT-RESULT-PARTIAL",
                f"The script task stopped before it finished: {file} is not marked "
                f"complete.{where} The project may not have been saved. Events that are "
                "not confirmed below must be mapped by hand.",
                file=file,
            ),
        )

    outcomes: list[EventOutcome] = []
    for entry in entries:
        if not isinstance(entry, Mapping) or not _text(entry.get("event_path")):
            findings.append(
                _finding(
                    "DVR-SCRIPT-RESULT-MALFORMED",
                    f"The script result file {file} holds an event entry without "
                    "event_path; it is ignored.",
                    file=file,
                )
            )
            continue
        outcomes.append(
            EventOutcome(
                event_path=_text(entry.get("event_path")),
                task=_text(entry.get("task")),
                status=_text(entry.get("status")) or STATUS_FAILED,
                message=_text(entry.get("message")),
            )
        )
    by_path = {outcome.event_path: outcome for outcome in outcomes}

    unconfirmed: list[ScriptEvent] = []
    confirmed = 0
    for event in wanted:
        outcome = by_path.get(event.event_path)
        label = f"the event {event.event_path} (SWC {event.swc}, runnable {event.runnable})"
        if outcome is None:
            unconfirmed.append(event)
            findings.append(
                _finding(
                    "DVR-SCRIPT-EVENT-MISSING",
                    f"The script result says nothing about {label}, which was to be "
                    f"mapped to the task {event.task}. {_BY_HAND}",
                    obj=event.event_path,
                    file=file,
                )
            )
        elif outcome.status not in _GOOD_STATUSES:
            unconfirmed.append(event)
            reason = outcome.message or f"status '{outcome.status}'"
            findings.append(
                _finding(
                    "DVR-SCRIPT-EVENT-FAILED",
                    f"The script could not map {label} to the task {event.task}: "
                    f"{reason}. {_BY_HAND}",
                    obj=event.event_path,
                    file=file,
                )
            )
        elif outcome.task != event.task:
            unconfirmed.append(event)
            findings.append(
                _finding(
                    "DVR-SCRIPT-TASK-MISMATCH",
                    f"The script reports {label} as mapped to the task "
                    f"{outcome.task or '(none)'}, but the task {event.task} was asked "
                    "for. Correct the mapping by hand in DaVinci Configurator.",
                    obj=event.event_path,
                    file=file,
                )
            )
        else:
            confirmed += 1

    changed = any(outcome.status == STATUS_MAPPED for outcome in outcomes)
    if complete and changed and not saved:
        findings.append(
            _finding(
                "DVR-SCRIPT-NOT-SAVED",
                "The script mapped events but did not save the project, so the mapping "
                "is lost. Look at the script steps above and at the log of DVCfgCmd.",
                file=file,
            )
        )
    solving = document.get("solving_actions")
    if isinstance(solving, Mapping):
        remaining = solving.get("remaining_errors")
        failed = solving.get("failed")
        if (isinstance(remaining, int) and remaining > 0) or (isinstance(failed, int) and failed):
            findings.append(
                _finding(
                    "DVR-SCRIPT-VALIDATION",
                    f"After the solving actions the RTE and OS validation still shows "
                    f"{remaining if isinstance(remaining, int) else '?'} error(s); "
                    f"{failed if isinstance(failed, int) else 0} solving action(s) failed. "
                    "Open the project in DaVinci Configurator and look at the validation "
                    "view.",
                    severity=Severity.WARNING,
                    file=file,
                )
            )
    ok = not any(finding.severity is Severity.ERROR for finding in findings)
    if ok and wanted:
        findings.append(
            _finding(
                "DVR-SCRIPT-OK",
                f"The script confirmed {confirmed} of {len(wanted)} event(s) as mapped; "
                f"project saved: {'yes' if saved else 'no'}.",
                severity=Severity.INFO,
                file=file,
            )
        )
    return ScriptResult(
        ok=ok,
        complete=complete,
        saved=saved,
        events=tuple(outcomes),
        findings=tuple(findings),
        unconfirmed=tuple(unconfirmed),
    )


def manual_mapping_instructions(events: Sequence[ScriptEvent]) -> list[str]:
    """Step-by-step text for mapping ``events`` by hand in DaVinci Configurator.

    The pipeline shows this whenever the script step is disabled or failed. The names of
    editors and menus can differ a little between Configurator versions; the data to
    enter (event, task, position) is exact.
    """
    if not events:
        return ["No event has to be mapped to a task."]
    lines = [
        f"Map {len(events)} new RTE event(s) to their task(s) by hand in DaVinci "
        "Configurator:",
        "1. Close DaVinci Developer. Open the project file (.dpa) in DaVinci Configurator "
        "and wait until the workspace changes have been read in (the Configurator "
        "updates its input files when the project is loaded).",
        "2. Open the task mapping of the RTE: Runtime System, then Task Mapping (in some "
        "versions: the Rte module in the Basic Editor, container RteSwComponentInstance, "
        "RteEventToTaskMapping).",
        "3. Map each event below. Unmapped events are listed there by SWC and runnable.",
    ]
    for number, event in enumerate(events, start=1):
        if event.position is not None:
            place = f"at position {event.position}"
        elif event.position_after_event:
            place = f"directly after the event {event.position_after_event}"
        else:
            place = "after the last event of that task"
        lines.append(
            f"   3.{number} SWC {event.swc}, runnable {event.runnable}, event "
            f"{event.event_path}: map it to the task {event.task}, {place}."
        )
    lines += [
        "4. Open the validation view. For every message of the RTE and of the OS that "
        "offers a preferred solving action, apply it (right-click the message, or use "
        "'Solve all' for RTE and OS messages). Messages that stay must be solved by hand.",
        "5. Save the project and close DaVinci Configurator.",
        "6. Run PortTool again: it reads the mapping from the ECUC file and goes on with "
        "validation and generation.",
    ]
    return lines
