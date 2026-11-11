"""Which ECUC containers hold the task mapping and the OS tasks.

PENDING-DISCOVERY: the exact definition paths of the Vector MICROSAR Rte and Os modules
are not confirmed (docs/discovery.md does not exist yet). The names below are the
AUTOSAR standard module definition names. They are matched as path *suffixes* of the
DEFINITION-REF, so ``/MICROSAR/Os/OsTask`` and ``/AUTOSAR/EcucDefs/Os/OsTask`` both
match. When discovery confirms the real paths, change the defaults here; nothing else
in the reader names an ECUC definition.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EcucLayout:
    """Definition-path suffixes the reader looks for in the ECUC file."""

    # PENDING-DISCOVERY: container of one OS task; its SHORT-NAME is the task name.
    os_task: str = "/Os/OsTask"
    # PENDING-DISCOVERY: container that maps one RTE event of one SWC instance to a task.
    event_mapping: str = "/Rte/RteSwComponentInstance/RteEventToTaskMapping"
    # PENDING-DISCOVERY: reference from the mapping to the RTE event (absolute path).
    event_ref: str = "/RteEventToTaskMapping/RteEventRef"
    # PENDING-DISCOVERY: reference from the mapping to the OsTask container.
    task_ref: str = "/RteEventToTaskMapping/RteMappedToTaskRef"
    # PENDING-DISCOVERY: integer parameter, position of the event inside the task.
    position: str = "/RteEventToTaskMapping/RtePositionInTask"

    @property
    def container_suffixes(self) -> tuple[str, ...]:
        """Suffixes of the container definitions that extraction keeps."""
        return (self.os_task, self.event_mapping)

    @property
    def signature(self) -> str:
        """Changes when extraction would keep other containers: part of the cache version."""
        return "|".join(self.container_suffixes)
