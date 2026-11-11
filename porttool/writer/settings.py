"""The ``[writer]`` table of porttool.toml.

PENDING-DISCOVERY: every default below is a guess about what DaVinci Developer writes.
They are settings so that the owner can change them after ``porttool discover``
without a new build.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from types import MappingProxyType

from porttool.core.config import ProjectConfig
from porttool.core.models import AccessKind, TriggerKind
from porttool.core.results import Finding, Severity
from porttool.writer import codes

PLACEMENTS = ("existing_file", "new_file")
UUID_MODES = ("auto", "upper", "lower", "none")

_ACCESS_PREFIXES: Mapping[str, str] = MappingProxyType(
    {
        AccessKind.IMPLICIT_READ.value: "DRA",
        AccessKind.IMPLICIT_WRITE.value: "DWA",
        AccessKind.EXPLICIT_SEND.value: "DSP",
        AccessKind.EXPLICIT_READ.value: "DRP",
        AccessKind.QUEUED_RECEIVE.value: "DRP",
        AccessKind.SYNC_SERVER_CALL.value: "SSCP",
        AccessKind.ASYNC_SERVER_CALL.value: "ASCP",
    }
)
_EVENT_PATTERNS: Mapping[str, str] = MappingProxyType(
    {
        TriggerKind.PERIODIC.value: "TMT_{runnable}",
        TriggerKind.DATA_RECEIVED.value: "DRT_{runnable}_{port}_{target}",
        TriggerKind.DATA_RECEIVE_ERROR.value: "DRET_{runnable}_{port}_{target}",
        TriggerKind.OPERATION_INVOKED.value: "OIT_{runnable}_{port}_{target}",
        TriggerKind.INIT.value: "IT_{runnable}",
    }
)


@dataclass(frozen=True, slots=True)
class WriterSettings:
    #: Where new data types and interfaces go: into the file that holds their package,
    #: or into a new file ``<folder>/<Name>.arxml``.
    new_object_placement: str = "existing_file"
    #: UUID attribute of new elements: like the siblings ("auto"), "upper", "lower", "none".
    uuid: str = "auto"
    #: Short name of a new access point. Placeholders: {prefix} {runnable} {port} {target}.
    access_point_pattern: str = "{prefix}_{port}_{target}"
    #: {prefix} per access kind (key: the text of ``AccessKind``).
    access_point_prefixes: Mapping[str, str] = field(default_factory=lambda: _ACCESS_PREFIXES)
    #: Short name of a new event per trigger kind (key: the text of ``TriggerKind``).
    #: Placeholders: {runnable} {port} {target}.
    event_patterns: Mapping[str, str] = field(default_factory=lambda: _EVENT_PATTERNS)
    #: Write a com spec for every data element or operation, not only for those with an init value.
    com_spec_for_every_element: bool = True
    #: Add ALIVE-TIMEOUT, ENABLE-UPDATE, HANDLE-NEVER-RECEIVED, HANDLE-TIMEOUT-TYPE to
    #: a new NONQUEUED-RECEIVER-COM-SPEC.
    receiver_com_spec_defaults: bool = True
    #: Write a CLIENT-COM-SPEC per operation into a new client port.
    client_com_spec: bool = True
    #: QUEUE-LENGTH of new QUEUED-RECEIVER-COM-SPEC and SERVER-COM-SPEC.
    queue_length: int = 1
    #: TIMEOUT of new server call points, in seconds.
    server_call_timeout: str = "0"
    #: Write SW-IMPL-POLICY STANDARD into data elements that are not queued.
    standard_impl_policy: bool = False
    #: Short names of the compu method and data constraint created for a new data type.
    compu_method_pattern: str = "CM_{name}"
    constraint_pattern: str = "DC_{name}"
    #: Short name of the sub-element of a new implementation array type.
    array_element_name: str = "Element"
    #: Path of a DATA-TYPE-MAPPING-SET. If set, a new application data type whose
    #: ``base_type_path`` names an implementation data type gets a DATA-TYPE-MAP there.
    data_type_mapping_set: str = ""

    @classmethod
    def from_config(cls, config: ProjectConfig) -> tuple[WriterSettings, tuple[Finding, ...]]:
        """Read ``[writer]``. A wrong value gives a warning and keeps the default."""
        table = config.extra("writer")
        defaults = cls()
        values: dict[str, object] = {}
        findings: list[Finding] = []

        def warn(message: str) -> None:
            findings.append(Finding(codes.SETTING, Severity.WARNING, f"[writer] {message}"))

        known = {item.name: item for item in fields(cls)}
        for key, value in table.items():
            if key not in known:
                warn(f"unknown setting {key!r} is ignored.")
                continue
            default = getattr(defaults, key)
            if isinstance(default, bool):
                if not isinstance(value, bool):
                    warn(f"{key} must be true or false; using {str(default).lower()}.")
                    continue
            elif isinstance(default, int):
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    warn(f"{key} must be a whole number of 0 or more; using {default}.")
                    continue
            elif isinstance(default, str):
                if not isinstance(value, str):
                    warn(f"{key} must be text; using {default!r}.")
                    continue
            else:
                if not isinstance(value, Mapping) or not all(
                    isinstance(k, str) and isinstance(v, str) for k, v in value.items()
                ):
                    warn(f"{key} must be a table of texts; using the defaults.")
                    continue
                value = MappingProxyType({**default, **value})
            values[key] = value
        for key, allowed in (("new_object_placement", PLACEMENTS), ("uuid", UUID_MODES)):
            if key in values and values[key] not in allowed:
                warn(f"{key} must be one of {', '.join(allowed)}; using {getattr(defaults, key)!r}.")
                del values[key]
        return cls(**values), tuple(findings)  # type: ignore[arg-type]
