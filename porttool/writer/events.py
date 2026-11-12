"""The ``WRT-INFO-EVENT`` finding: how the writer tells the pipeline which RTE events it made.

The writer never maps an event to a task (that is done through DaVinci Configurator).
It reports each new event as one info finding. ``created_events`` turns those findings
back into data, so that the pipeline does not have to read message text itself.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from porttool.core.results import Finding
from porttool.writer import codes

_NONE = "none"
_MESSAGE = re.compile(
    r"\[op (?P<op>.*?)\] Created (?P<tag>\S+) (?P<path>\S+) for runnable (?P<runnable>.+?); "
    r"requested task: (?P<task>.+?); after event: (?P<after>.+?)\.\Z",
    re.DOTALL,
)


@dataclass(frozen=True, slots=True)
class CreatedEvent:
    operation_id: str
    path: str  # absolute path of the new event
    element: str  # TIMING-EVENT, DATA-RECEIVED-EVENT, ...
    runnable: str  # short name of the runnable the event starts
    task: str | None  # requested OS task, None: leave unmapped
    position_after_event: str | None  # path of the event this one is placed after
    file: str | None  # repo-relative file the event was written to


def event_message(tag: str, path: str, runnable: str, task: str | None, after: str | None) -> str:
    """Message text of the finding, without the ``[op <id>]`` prefix."""
    return (
        f"Created {tag} {path} for runnable {runnable}; requested task: {task or _NONE}; "
        f"after event: {after or _NONE}."
    )


def created_events(findings: Iterable[Finding]) -> tuple[CreatedEvent, ...]:
    """The events reported in ``WriteOutcome.findings``, in the order they were created."""
    events: list[CreatedEvent] = []
    for finding in findings:
        if finding.code != codes.INFO_EVENT:
            continue
        match = _MESSAGE.match(finding.message)
        if match is None:
            continue
        task, after = match.group("task"), match.group("after")
        events.append(
            CreatedEvent(
                operation_id=match.group("op"),
                path=match.group("path"),
                element=match.group("tag"),
                runnable=match.group("runnable"),
                task=None if task == _NONE else task,
                position_after_event=None if after == _NONE else after,
                file=finding.file,
            )
        )
    return tuple(events)
