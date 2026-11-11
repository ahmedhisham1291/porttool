"""The ``[pipeline]`` table of porttool.toml."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields

from porttool.core.config import ProjectConfig
from porttool.core.results import Finding, Severity
from porttool.pipeline import codes

#: DaVinci steps in the order they run, whatever the order in the configuration.
DAVINCI_STEPS: tuple[str, ...] = ("check", "script", "validate", "generate")


@dataclass(frozen=True, slots=True)
class PipelineSettings:
    #: Enabled DaVinci steps. Default: none (edit, verify by reload, commit, stop).
    davinci_steps: tuple[str, ...] = ()
    #: When the workspace check fails: undo the direct edit and import a delta ARXML.
    fallback_import: bool = False
    #: Delete requests are refused unless this is true.
    allow_delete: bool = False
    #: Base folder of the reports, repo-relative or absolute. Reports go to
    #: ``<report_dir>/<branch name>/``.
    report_dir: str = "porttool_reports"
    #: Commit the report folder together with the change.
    commit_reports: bool = False
    #: Commit files the DaVinci tools changed in tracked folders (ECUC, generated code).
    commit_generated: bool = True
    #: Folders or files (repo-relative) the DaVinci tools may write. Empty: the ARXML
    #: roots, the folder of the ECUC file, the .dpa file and the generated folder.
    tool_write_scopes: tuple[str, ...] = ()
    #: Folder of the journals. Empty: %LOCALAPPDATA%\PortTool\journals.
    journal_dir: str = ""
    #: Lines kept in the log tail of a job.
    log_tail_lines: int = 200

    def enabled(self, step: str) -> bool:
        return step in self.davinci_steps

    @property
    def any_davinci_step(self) -> bool:
        return bool(self.davinci_steps)


def _finding(code: str, message: str, severity: Severity = Severity.ERROR) -> Finding:
    return Finding(code=code, severity=severity, message=message, file="porttool.toml")


def read_settings(config: ProjectConfig) -> tuple[PipelineSettings, tuple[Finding, ...]]:
    """Read ``[pipeline]``. A wrong value gives an error finding and keeps the default."""
    table: Mapping[str, object] = config.extra("pipeline")
    findings: list[Finding] = []
    values: dict[str, object] = {}
    defaults = PipelineSettings()
    known = {item.name for item in fields(PipelineSettings)}
    for key, value in table.items():
        if key not in known:
            findings.append(
                _finding(
                    codes.CFG_UNKNOWN_KEY,
                    f"[pipeline] {key} is not a known setting and is ignored.",
                    Severity.WARNING,
                )
            )
            continue
        default = getattr(defaults, key)
        if isinstance(default, bool):
            ok = isinstance(value, bool)
        elif isinstance(default, int):
            ok = isinstance(value, int) and not isinstance(value, bool) and value > 0
        elif isinstance(default, str):
            ok = isinstance(value, str)
        else:
            ok = isinstance(value, list | tuple) and all(isinstance(x, str) for x in value)
        if not ok:
            findings.append(
                _finding(
                    codes.CFG_TYPE,
                    f"[pipeline] {key} has a value of the wrong type ({value!r}); "
                    f"the default {default!r} is used.",
                )
            )
            continue
        values[key] = tuple(value) if isinstance(value, list | tuple) else value
    steps = values.get("davinci_steps", ())
    if isinstance(steps, tuple):
        unknown = [step for step in steps if step not in DAVINCI_STEPS]
        for step in unknown:
            findings.append(
                _finding(
                    codes.CFG_UNKNOWN_STEP,
                    f'[pipeline] davinci_steps names "{step}"; known steps are '
                    f"{', '.join(DAVINCI_STEPS)}.",
                )
            )
        values["davinci_steps"] = tuple(step for step in DAVINCI_STEPS if step in steps)
    settings = PipelineSettings(**values)  # type: ignore[arg-type]
    return settings, tuple(findings)
