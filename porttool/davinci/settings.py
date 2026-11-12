"""The ``[davinci]`` table of porttool.toml as a typed settings object.

Nobody on this project has seen the real flags, output or exit codes of the Vector
tools. So everything about them is a setting, and every built-in default below is a
guess, labelled UNVERIFIED wherever it is shown. ``load_settings`` never raises for a
bad value: it reports a Finding (code prefix ``DVR-``) and falls back to the default.
"""

from __future__ import annotations

import codecs
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from porttool.core.config import ProjectConfig
from porttool.core.results import Finding, Severity
from porttool.davinci.templates import CommandTemplate, parse_template

TABLE = "davinci"

#: The actions PortTool can ask of the Vector tools, and the tool behind each.
TOOL_OF_ACTION: Mapping[str, str] = MappingProxyType(
    {
        "check": "DVWspChecker",
        "import": "DVImEx",
        "contract_headers": "DVSwcGen",
        "script_task": "DVCfgCmd",
        "validate": "DVCfgCmd",
        "generate": "DVCfgCmd",
    }
)
ACTIONS: tuple[str, ...] = tuple(TOOL_OF_ACTION)
TOOLS: tuple[str, ...] = ("DVWspChecker", "DVImEx", "DVSwcGen", "DVCfgCmd")
#: Key of each tool in ``[davinci.executables]``.
EXECUTABLE_KEYS: Mapping[str, str] = MappingProxyType({tool.lower(): tool for tool in TOOLS})
#: The ``[pipeline] davinci_steps`` name that needs each action (docs/SHARED_CONTEXT.md).
STEP_OF_ACTION: Mapping[str, str] = MappingProxyType(
    {"check": "check", "script_task": "script", "validate": "validate", "generate": "generate"}
)

#: UNVERIFIED: built-in command templates. Every flag below is a guess that nobody has
#: checked against the real tool. They are used only until the owner writes the action
#: in [davinci.commands] of porttool.toml, and they are labelled UNVERIFIED by
#: `porttool davinci doctor` and `porttool davinci show-commands`.
BUILTIN_COMMANDS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        # UNVERIFIED: DVWspChecker flag for the workspace file.
        "check": ("{exe}", "-w", "{dcf}"),
        # UNVERIFIED: DVImEx flags for workspace and import file; import mode unknown.
        "import": ("{exe}", "-w", "{dcf}", "-i", "{arxml}"),
        # UNVERIFIED: DVSwcGen flags for workspace and component names.
        "contract_headers": ("{exe}", "-w", "{dcf}", "-c", "{swcs}"),
        # UNVERIFIED: DVCfgCmd flags for project, script task and script folder. The
        # request and result file reach the script through environment variables
        # (PORTTOOL_REQUEST_JSON, PORTTOOL_RESULT_JSON), not through flags.
        "script_task": (
            "{exe}",
            "-p",
            "{dpa}",
            "-s",
            "{script_task}",
            "--scriptLocations",
            "{script_dir}",
        ),
        # UNVERIFIED: no validate-only flag of DVCfgCmd is known, so the guess is the
        # script task PortToolValidate of the same Groovy file.
        "validate": (
            "{exe}",
            "-p",
            "{dpa}",
            "-s",
            "PortToolValidate",
            "--scriptLocations",
            "{script_dir}",
        ),
        # UNVERIFIED: DVCfgCmd flags for generation and the module list; the module
        # names may have to be definition paths such as /MICROSAR/Rte.
        "generate": ("{exe}", "-p", "{dpa}", "-g", "-m", "{modules_csv}"),
    }
)

DEFAULT_SUCCESS_EXIT_CODES: tuple[int, ...] = (0,)
DEFAULT_ERROR_PATTERNS: tuple[str, ...] = ("(?i)error",)
DEFAULT_WARNING_PATTERNS: tuple[str, ...] = ("(?i)warning",)
_LICENSE_TROUBLE = (
    "(?:not found|missing|expired|unavailable|could not|cannot|unreachable|not available"
    "|no valid|invalid|denied|failed)"
)
#: PENDING-DISCOVERY: the real license messages of the Vector tools are not known.
DEFAULT_LICENSE_PATTERNS: tuple[str, ...] = (
    rf"(?i)licen[sc]e.{{0,80}}{_LICENSE_TROUBLE}|{_LICENSE_TROUBLE}.{{0,80}}licen[sc]e",
)
#: Text cut out of a line before the error and warning patterns are tried, so that a
#: summary such as "0 errors, 2 warnings" counts as a warning line and not as an error.
#: Applied in this order: "Errors: 0  Warnings: 10" must lose "Errors: 0" first.
DEFAULT_IGNORE_PATTERNS: tuple[str, ...] = (
    r"(?i)\b(?:error|warning)s?(?:\(s\))?\s*[:=]\s*0\b",
    r"(?i)\b(?:0|no)\s+(?:error|warning)s?\b",
)
DEFAULT_MAX_KEPT_OUTPUT_LINES = 2000
#: PENDING-DISCOVERY: process names of the DaVinci GUIs.
DEFAULT_GUI_PROCESS_NAMES: tuple[str, ...] = ("DaVinciDEV.exe", "DaVinciCFG.exe", "DVCfg.exe")
#: PENDING-DISCOVERY: names of the lock files DaVinci writes next to .dcf and .dpa.
DEFAULT_LOCK_FILE_GLOBS: tuple[str, ...] = ("*.lock", "*.lck", "~*")
DEFAULT_SCRIPT_TASK = "PortToolMapEvents"
DEFAULT_SCRIPT_DIR = Path(__file__).resolve().parent / "groovy"
SCRIPT_FILE_NAME = "PortToolTasks.dvgroovy"
DEFAULT_MAX_TOOL_LOGS = 200
LEFTOVER_CHOICES: tuple[str, ...] = ("kill", "report")


@dataclass(frozen=True, slots=True)
class ResultRules:
    """How the outcome of a tool run is judged. Table ``[davinci.results]``."""

    success_exit_codes: Mapping[str, tuple[int, ...]]
    error_patterns: tuple[re.Pattern[str], ...]
    warning_patterns: tuple[re.Pattern[str], ...]
    license_patterns: tuple[re.Pattern[str], ...]
    ignore_patterns: tuple[re.Pattern[str], ...]
    report_globs: tuple[str, ...] = ()

    def exit_codes_for(self, action: str) -> tuple[int, ...]:
        return self.success_exit_codes.get(action, DEFAULT_SUCCESS_EXIT_CODES)


@dataclass(frozen=True, slots=True)
class DaVinciSettings:
    """Everything the davinci package reads from porttool.toml."""

    executables: Mapping[str, str]  # tool name -> configured path text, "" = search
    commands: Mapping[str, CommandTemplate]
    results: ResultRules
    encoding: str = "auto"  # "auto": UTF-8, then the ANSI code page; or a codec name
    max_kept_output_lines: int = DEFAULT_MAX_KEPT_OUTPUT_LINES
    gui_process_names: tuple[str, ...] = DEFAULT_GUI_PROCESS_NAMES
    lock_file_globs: tuple[str, ...] = DEFAULT_LOCK_FILE_GLOBS
    script_dir: Path = DEFAULT_SCRIPT_DIR
    script_task: str = DEFAULT_SCRIPT_TASK
    env: Mapping[str, str] = MappingProxyType({})
    log_dir: Path | None = None  # None: tools/ below the PortTool log folder
    leftover_children: str = "kill"
    max_tool_logs: int = DEFAULT_MAX_TOOL_LOGS
    enabled_steps: tuple[str, ...] = ()  # [pipeline] davinci_steps, for doctor's severity

    def unverified_actions(self) -> tuple[str, ...]:
        return tuple(a for a in ACTIONS if not self.commands[a].verified)


def _describe(value: object) -> str:
    if isinstance(value, bool):
        return "true or false"
    if isinstance(value, str):
        return "a text"
    if isinstance(value, int | float):
        return "a number"
    if isinstance(value, Mapping):
        return "a table"
    if isinstance(value, Sequence):
        return "a list"
    return type(value).__name__


class _Table:
    """Reads one TOML table, records every problem, and reports unknown keys."""

    def __init__(self, name: str, data: object, findings: list[Finding]) -> None:
        self.name = name
        self._findings = findings
        if isinstance(data, Mapping):
            self._data: Mapping[str, object] = data
        else:
            self._data = {}
            if data is not None:
                self._wrong("", "a table", data)
        self._used: set[str] = set()

    def _path(self, key: str) -> str:
        return f"{self.name}.{key}" if key else self.name

    def problem(self, code: str, key: str, message: str, severity: Severity) -> None:
        self._findings.append(Finding(code, severity, message, object_path=self._path(key)))

    def _wrong(self, key: str, expected: str, value: object) -> None:
        where = f"[{self.name}] {key}" if key else f"[{self.name}]"
        self.problem(
            "DVR-CFG-TYPE",
            key,
            f"{where} in porttool.toml must be {expected}, but it is {_describe(value)}. "
            "The built-in default is used instead.",
            Severity.ERROR,
        )

    def has(self, key: str) -> bool:
        return key in self._data

    def take(self, key: str) -> object | None:
        self._used.add(key)
        return self._data.get(key)

    def keys(self) -> tuple[str, ...]:
        return tuple(self._data)

    def text(self, key: str, default: str) -> str:
        value = self.take(key)
        if value is None:
            return default
        if not isinstance(value, str):
            self._wrong(key, "a text in quotes", value)
            return default
        return value.strip()

    def integer(self, key: str, default: int, minimum: int) -> int:
        value = self.take(key)
        if value is None:
            return default
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            self._wrong(key, f"a whole number of at least {minimum}", value)
            return default
        return value

    def text_list(self, key: str, default: tuple[str, ...]) -> tuple[str, ...]:
        value = self.take(key)
        if value is None:
            return default
        if (
            isinstance(value, str)
            or not isinstance(value, Sequence)
            or not all(isinstance(item, str) for item in value)
        ):
            self._wrong(key, 'a list of texts, for example ["a", "b"]', value)
            return default
        return tuple(str(item) for item in value)

    def table(self, key: str) -> _Table:
        return _Table(self._path(key), self.take(key), self._findings)

    def finish(self, known_hint: str = "") -> None:
        for key in self._data:
            if key not in self._used:
                self.problem(
                    "DVR-CFG-UNKNOWN",
                    key,
                    f"[{self.name}] {key} in porttool.toml is not a known setting and is "
                    f"ignored.{known_hint}",
                    Severity.WARNING,
                )


def _patterns(
    table: _Table, key: str, default: tuple[str, ...]
) -> tuple[re.Pattern[str], ...]:
    compiled: list[re.Pattern[str]] = []
    for text in table.text_list(key, default):
        try:
            compiled.append(re.compile(text))
        except re.error as error:
            table.problem(
                "DVR-CFG-REGEX",
                key,
                f"[{table.name}] {key} in porttool.toml holds the regular expression "
                f"{text!r}, which is not valid: {error}. No DaVinci tool is started until "
                "it is corrected.",
                Severity.ERROR,
            )
    return tuple(compiled)


def _exit_codes(table: _Table) -> Mapping[str, tuple[int, ...]]:
    codes: dict[str, tuple[int, ...]] = {}
    for action in table.keys():
        value = table.take(action)
        if action not in ACTIONS:
            table.problem(
                "DVR-CFG-UNKNOWN",
                action,
                f"[{table.name}] {action} in porttool.toml is not an action and is ignored. "
                f"Actions: {', '.join(ACTIONS)}.",
                Severity.WARNING,
            )
            continue
        if (
            isinstance(value, str)
            or not isinstance(value, Sequence)
            or not value
            or not all(isinstance(item, int) and not isinstance(item, bool) for item in value)
        ):
            table.problem(
                "DVR-CFG-TYPE",
                action,
                f"[{table.name}] {action} in porttool.toml must be a list of whole numbers, "
                f"for example {action} = [0, 1]. The default [0] is used instead.",
                Severity.ERROR,
            )
            continue
        codes[action] = tuple(int(item) for item in value)
    return MappingProxyType(codes)


def _absolute(repo_root: Path, text: str) -> Path:
    path = Path(text)
    return path if path.is_absolute() else repo_root / path


def _enabled_steps(config: ProjectConfig) -> tuple[str, ...]:
    steps = config.extra("pipeline").get("davinci_steps")
    if isinstance(steps, Sequence) and not isinstance(steps, str):
        return tuple(str(step) for step in steps)
    return ()


def load_settings(config: ProjectConfig) -> tuple[DaVinciSettings, tuple[Finding, ...]]:
    """Parse ``[davinci]``. Problems come back as Findings, never as an exception."""
    findings: list[Finding] = []
    root = _Table(TABLE, config.extra(TABLE), findings)

    exe_table = root.table("executables")
    executables = {tool: exe_table.text(key, "") for key, tool in EXECUTABLE_KEYS.items()}
    exe_table.finish(f" Known tools: {', '.join(EXECUTABLE_KEYS)}.")

    command_table = root.table("commands")
    commands: dict[str, CommandTemplate] = {}
    for action in ACTIONS:
        if command_table.has(action):
            template, problems = parse_template(
                action, command_table.take(action), verified=True
            )
            findings.extend(problems)
        else:
            template = CommandTemplate(action, BUILTIN_COMMANDS[action], verified=False)
        commands[action] = template
    command_table.finish(f" Actions: {', '.join(ACTIONS)}.")

    result_table = root.table("results")
    rules = ResultRules(
        success_exit_codes=_exit_codes(result_table.table("success_exit_codes")),
        error_patterns=_patterns(result_table, "error_patterns", DEFAULT_ERROR_PATTERNS),
        warning_patterns=_patterns(result_table, "warning_patterns", DEFAULT_WARNING_PATTERNS),
        license_patterns=_patterns(result_table, "license_patterns", DEFAULT_LICENSE_PATTERNS),
        ignore_patterns=_patterns(result_table, "ignore_patterns", DEFAULT_IGNORE_PATTERNS),
        report_globs=result_table.text_list("report_globs", ()),
    )
    result_table.finish()

    encoding = root.text("encoding", "auto") or "auto"
    if encoding.lower() == "auto":
        encoding = "auto"
    else:
        try:
            codecs.lookup(encoding)
        except LookupError:
            root.problem(
                "DVR-CFG-ENCODING",
                "encoding",
                f"[davinci] encoding = {encoding!r} in porttool.toml is not an encoding "
                'Python knows. Use "auto", or a name such as "cp1252", "cp850" or "utf-8". '
                '"auto" is used instead.',
                Severity.ERROR,
            )
            encoding = "auto"

    env_table = root.table("env")
    env = {key: env_table.text(key, "") for key in env_table.keys()}

    leftover = root.text("leftover_children", "kill").lower()
    if leftover not in LEFTOVER_CHOICES:
        root.problem(
            "DVR-CFG-TYPE",
            "leftover_children",
            f'[davinci] leftover_children in porttool.toml must be "kill" or "report", but '
            f'it is {leftover!r}. "kill" is used instead.',
            Severity.ERROR,
        )
        leftover = "kill"

    script_dir_text = root.text("script_dir", "")
    log_dir_text = root.text("log_dir", "")
    settings = DaVinciSettings(
        executables=MappingProxyType(executables),
        commands=MappingProxyType(commands),
        results=rules,
        encoding=encoding,
        max_kept_output_lines=root.integer(
            "max_kept_output_lines", DEFAULT_MAX_KEPT_OUTPUT_LINES, 1
        ),
        gui_process_names=root.text_list("gui_process_names", DEFAULT_GUI_PROCESS_NAMES),
        lock_file_globs=root.text_list("lock_file_globs", DEFAULT_LOCK_FILE_GLOBS),
        script_dir=(
            _absolute(config.repo_root, script_dir_text) if script_dir_text else DEFAULT_SCRIPT_DIR
        ),
        script_task=root.text("script_task", DEFAULT_SCRIPT_TASK) or DEFAULT_SCRIPT_TASK,
        env=MappingProxyType(env),
        log_dir=_absolute(config.repo_root, log_dir_text) if log_dir_text else None,
        leftover_children=leftover,
        max_tool_logs=root.integer("max_tool_logs", DEFAULT_MAX_TOOL_LOGS, 1),
        enabled_steps=_enabled_steps(config),
    )
    root.finish()
    return settings, tuple(findings)


def blocking_findings(findings: Sequence[Finding], action: str) -> tuple[Finding, ...]:
    """The setting errors that forbid running ``action``.

    An error in the template of another action does not block this one; every other
    error (patterns, exit codes, encoding) does, because the result could not be judged.
    """
    prefix = f"{TABLE}.commands."
    blocking: list[Finding] = []
    for finding in findings:
        if finding.severity is not Severity.ERROR:
            continue
        path = finding.object_path or ""
        if path.startswith(prefix) and path != prefix + action:
            continue
        blocking.append(finding)
    return tuple(blocking)
