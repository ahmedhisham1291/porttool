"""``porttool davinci doctor``: what the owner has to fix before a DaVinci step can run.

The doctor starts NO tool. It looks at files and settings only, and every finding that
asks for a change names the exact porttool.toml line to add.
"""

from __future__ import annotations

import os
from pathlib import Path

from porttool.core.results import Finding, Severity
from porttool.davinci.executables import read_file_version
from porttool.davinci.gui import ProcessLister, davinci_gui_findings
from porttool.davinci.runner import DaVinciToolRunner, missing_executable
from porttool.davinci.settings import (
    ACTIONS,
    SCRIPT_FILE_NAME,
    STEP_OF_ACTION,
    TOOL_OF_ACTION,
    TOOLS,
)
from porttool.davinci.templates import placeholders_in, toml_list, toml_string

_PATH_EXAMPLES = {"dcf": "path/to/Workspace.dcf", "dpa": "path/to/Project.dpa"}


def _writable_ancestor(folder: Path) -> bool:
    """True if ``folder`` exists and is writable, or could be created."""
    current = folder
    while not current.exists():
        if current.parent == current:
            return False
        current = current.parent
    return current.is_dir() and os.access(current, os.W_OK)


def doctor_findings(
    runner: DaVinciToolRunner, *, processes: ProcessLister | None = None
) -> tuple[Finding, ...]:
    settings = runner.settings
    config = runner.config
    enabled = set(settings.enabled_steps)
    findings: list[Finding] = list(runner.settings_findings)

    def users_of(name: str) -> list[str]:
        return [
            action
            for action in ACTIONS
            if settings.commands[action].usable
            and name in placeholders_in(settings.commands[action].args)
        ]

    def severity_for(actions: list[str]) -> Severity:
        needed = any(STEP_OF_ACTION.get(action) in enabled for action in actions)
        return Severity.ERROR if needed else Severity.WARNING

    # ---- the executables
    for tool in TOOLS:
        actions = [action for action in users_of("exe") if TOOL_OF_ACTION[action] == tool]
        lookup = runner.lookup(tool)
        if lookup.path is not None:
            version = read_file_version(lookup.path)
            findings.append(
                Finding(
                    "DVR-DOC-EXE",
                    Severity.INFO,
                    f"{tool}: found at {lookup.path} (from {lookup.source}); file version: "
                    f"{version or 'not readable'}.",
                    object_path=f"davinci.executables.{lookup.key}",
                    file=str(lookup.path),
                )
            )
        elif actions:
            base = missing_executable(lookup, severity_for(actions))
            findings.append(
                Finding(
                    base.code,
                    base.severity,
                    f"{base.message} Needed by the action(s): {', '.join(actions)}.",
                    object_path=base.object_path,
                )
            )

    # ---- the command templates
    for action in ACTIONS:
        template = settings.commands[action]
        tool = TOOL_OF_ACTION[action]
        if not template.usable:
            continue  # the settings finding above says what is wrong
        if template.verified:
            findings.append(
                Finding(
                    "DVR-DOC-TPL",
                    Severity.INFO,
                    f"The command line of the action {action} ({tool}) is set in "
                    f"porttool.toml: {toml_list(template.args)}",
                    object_path=f"davinci.commands.{action}",
                )
            )
        else:
            findings.append(
                Finding(
                    "DVR-TPL-UNVERIFIED",
                    Severity.WARNING,
                    f"The command line of the action {action} ({tool}) is a built-in guess "
                    "and UNVERIFIED: nobody has checked its flags against the real tool. "
                    f"Compare it with the help text of {tool} (`porttool discover run` "
                    "saves it), then confirm or correct it. Fix: in porttool.toml, table "
                    f"[davinci.commands], add the line: {action} = {toml_list(template.args)}",
                    object_path=f"davinci.commands.{action}",
                )
            )
    for prepared in runner.rendered_commands():
        for finding in prepared.findings:
            if finding.code == "DVR-TPL-UNRESOLVED" and not any(
                "{" + name + "}" in finding.message for name in ("exe", "dcf", "dpa")
            ):
                findings.append(finding)

    # ---- the workspace and the project file
    for name, path in (("dcf", config.dcf_file), ("dpa", config.dpa_file)):
        actions = users_of(name)
        if not actions:
            continue
        key = f"{name}_file"
        listed = ", ".join(actions)
        if path is None:
            findings.append(
                Finding(
                    "DVR-PATH-NOT-SET",
                    severity_for(actions),
                    f"The action(s) {listed} need {{{name}}}, but {key} is not set. Fix: in "
                    f"porttool.toml, table [paths], add the line: {key} = "
                    f"{toml_string(_PATH_EXAMPLES[name])}",
                    object_path=f"paths.{key}",
                )
            )
        elif not path.is_file():
            findings.append(
                Finding(
                    "DVR-PATH-MISSING",
                    severity_for(actions),
                    f"The action(s) {listed} need {{{name}}}, but the file {path} does not "
                    f"exist. Fix: in porttool.toml, table [paths], correct the line: {key} = "
                    f"{toml_string(_PATH_EXAMPLES[name])}",
                    object_path=f"paths.{key}",
                    file=str(path),
                )
            )
        else:
            findings.append(
                Finding(
                    "DVR-DOC-PATH",
                    Severity.INFO,
                    f"{{{name}}} is {path} (exists); used by the action(s): {listed}.",
                    object_path=f"paths.{key}",
                    file=str(path),
                )
            )

    # ---- the Groovy script
    actions = users_of("script_dir")
    if actions:
        script = settings.script_dir / SCRIPT_FILE_NAME
        if not script.is_file():
            findings.append(
                Finding(
                    "DVR-SCRIPT-FILE-MISSING",
                    severity_for(actions),
                    f"The action(s) {', '.join(actions)} need the script {script}, which "
                    "does not exist. Fix: in porttool.toml, table [davinci], remove the "
                    "line script_dir to use the folder that ships with PortTool, or point "
                    f"it at the folder that holds {SCRIPT_FILE_NAME}.",
                    object_path="davinci.script_dir",
                    file=str(script),
                )
            )
        else:
            findings.append(
                Finding(
                    "DVR-SCRIPT-UNVERIFIED",
                    Severity.WARNING,
                    f"The Groovy script {script} is EXPERIMENTAL and UNVERIFIED: its calls "
                    "into the DaVinci Configurator automation interface have never been "
                    "run. Read the README.md next to it before you enable the script "
                    "step; until then map new events by hand (PortTool prints the steps).",
                    object_path="davinci.script_dir",
                    file=str(script),
                )
            )

    # ---- the log folder
    log_dir = runner.log_dir()
    if _writable_ancestor(log_dir):
        findings.append(
            Finding("DVR-DOC-LOG", Severity.INFO, f"Tool logs are written to {log_dir}.")
        )
    else:
        example = toml_string(r"C:\some\writable\folder")
        findings.append(
            Finding(
                "DVR-LOG-DIR",
                Severity.ERROR,
                f"The log folder {log_dir} cannot be written. Fix: in porttool.toml, table "
                f"[davinci], add the line: log_dir = {example}",
                object_path="davinci.log_dir",
            )
        )

    findings.extend(davinci_gui_findings(config, processes=processes))
    findings.append(
        Finding(
            "DVR-DOC-NO-RUN",
            Severity.INFO,
            "doctor started no DaVinci tool. `porttool davinci show-commands` prints the "
            "command lines that would be used.",
        )
    )
    return tuple(findings)
