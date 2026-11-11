"""``DaVinciToolRunner``: the adapters for DVWspChecker, DVImEx, DVSwcGen and DVCfgCmd.

Every adapter does the same three things: render the command template of its action,
run it through ``procrun.run_process``, and turn the outcome into a ``ToolResult``.
Nothing about the real tools is fixed here; see ``settings.py``.
"""

from __future__ import annotations

import glob
import os
import shutil
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import count
from pathlib import Path

from porttool.core.config import ProjectConfig, ToolTimeouts
from porttool.core.errors import LicenseError
from porttool.core.interfaces import JobContext, ToolResult
from porttool.core.log import current_log_file, default_log_dir
from porttool.core.results import Finding, Severity
from porttool.davinci.executables import ExecutableLookup, find_executable
from porttool.davinci.procrun import (
    MAX_MATCHES,
    LineDecoder,
    LogLine,
    Matches,
    ProcessOutcome,
    ProcessRequest,
    judge_line,
    run_process,
    shorten,
)
from porttool.davinci.settings import (
    ACTIONS,
    EXECUTABLE_KEYS,
    TOOL_OF_ACTION,
    DaVinciSettings,
    blocking_findings,
    load_settings,
)
from porttool.davinci.templates import (
    RenderedCommand,
    Value,
    placeholders_in,
    render_command,
    toml_list,
    toml_string,
)

LOG_SUBFOLDER = "tools"
MAX_REPORT_FILES = 20
#: Placeholders whose value must be an existing file before a tool is started.
_FILE_PLACEHOLDERS: tuple[str, ...] = ("dcf", "dpa", "arxml", "request_json")
_sequence = count(1)


def timeout_for(action: str, timeouts: ToolTimeouts) -> float:
    """The timeout of an action. DVSwcGen has no own entry and uses the generation one."""
    return {
        "check": timeouts.workspace_check_s,
        "import": timeouts.import_s,
        "contract_headers": timeouts.generate_s,
        "script_task": timeouts.script_task_s,
        "validate": timeouts.validate_s,
        "generate": timeouts.generate_s,
    }[action]


def executable_fix(lookup: ExecutableLookup) -> str:
    """The exact porttool.toml line that tells PortTool where a tool is."""
    example = rf"C:\full\path\to\{lookup.tool}.exe"
    return (
        "Fix: in porttool.toml, table [davinci.executables], add the line: "
        f"{lookup.key} = {toml_string(example)}"
    )


def missing_executable(lookup: ExecutableLookup, severity: Severity = Severity.ERROR) -> Finding:
    if lookup.configured:
        where = (
            f"[davinci.executables] {lookup.key} = {toml_string(lookup.configured)} in "
            f"porttool.toml names a file that does not exist ({lookup.searched[0]})."
        )
    elif lookup.searched:
        where = f"{lookup.tool}.exe was not found. Looked for: {'; '.join(lookup.searched)}."
    else:
        where = (
            f"{lookup.tool}.exe was not found: neither developer_bin nor sip_root is set in "
            "the [paths] table of porttool.toml, so there is no folder to search."
        )
    return Finding(
        "DVR-EXE-MISSING",
        severity,
        f"{where} {executable_fix(lookup)}",
        object_path=f"davinci.executables.{lookup.key}",
    )


@dataclass(frozen=True, slots=True)
class PreparedCommand:
    """A command that is ready to run, or the findings that say why it is not."""

    action: str
    tool: str
    rendered: RenderedCommand | None
    findings: tuple[Finding, ...] = ()

    @property
    def runnable(self) -> bool:
        return self.rendered is not None and self.rendered.ok


def _hex_code(code: int) -> str:
    return f" (0x{code & 0xFFFFFFFF:08X})" if code < 0 or code > 255 else ""


def scan_report(path: Path, settings: DaVinciSettings) -> tuple[Matches, Matches, Matches]:
    """Error, warning and license lines of one report file written by a tool."""
    errors, warnings, licenses = Matches(), Matches(), Matches()
    decoder = LineDecoder(settings.encoding)
    number = 0

    def judge(lines: list[str]) -> None:
        nonlocal number
        for text in lines:
            number += 1
            kind = judge_line(settings.results, text)
            if kind is None:
                continue
            line = LogLine(number, shorten(text), "file")
            {"license": licenses, "error": errors, "warning": warnings}[kind].add(line)

    with open(path, "rb") as stream:
        while chunk := stream.read(1024 * 1024):
            judge(decoder.feed(chunk))
    judge(decoder.flush())
    return errors, warnings, licenses


class DaVinciToolRunner:
    """Implements ``porttool.core.interfaces.DaVinciRunner``."""

    def __init__(self, config: ProjectConfig, *, log_dir: Path | None = None) -> None:
        self._config = config
        self._settings, self._settings_findings = load_settings(config)
        self._log_dir = log_dir
        self._last: dict[str, ProcessOutcome] = {}

    # ------------------------------------------------------------- read-only views

    @property
    def config(self) -> ProjectConfig:
        return self._config

    @property
    def settings(self) -> DaVinciSettings:
        return self._settings

    @property
    def settings_findings(self) -> tuple[Finding, ...]:
        return self._settings_findings

    def log_dir(self) -> Path:
        """Folder of the tool logs: the argument, ``[davinci] log_dir``, or the PortTool logs."""
        if self._log_dir is not None:
            return self._log_dir
        if self._settings.log_dir is not None:
            return self._settings.log_dir
        current = current_log_file()
        base = current.parent if current is not None else default_log_dir()
        return base / LOG_SUBFOLDER

    def last_outcome(self, action: str) -> ProcessOutcome | None:
        """Details of the newest run of ``action`` (kept output lines, matches)."""
        return self._last.get(action)

    def lookup(self, tool: str) -> ExecutableLookup:
        return find_executable(tool, self._settings, self._config)

    # ------------------------------------------------------------- preparing a command

    def values(self, action: str, extra: Mapping[str, Value] | None = None) -> dict[str, Value]:
        """The value of every placeholder for ``action`` (None: not available)."""
        lookup = self.lookup(TOOL_OF_ACTION[action])
        config = self._config
        values: dict[str, Value] = {
            "exe": str(lookup.path) if lookup.path is not None else None,
            "dcf": str(config.dcf_file) if config.dcf_file is not None else None,
            "dpa": str(config.dpa_file) if config.dpa_file is not None else None,
            "arxml": None,
            "modules": None,
            "modules_csv": None,
            "swcs": None,
            "swcs_csv": None,
            "script_dir": str(self._settings.script_dir),
            "script_task": self._settings.script_task,
            "request_json": None,
            "result_json": None,
            "log_dir": str(self.log_dir()),
        }
        if extra:
            values.update(extra)
        return values

    def prepare(
        self, action: str, extra: Mapping[str, Value] | None = None, *, check_paths: bool = True
    ) -> PreparedCommand:
        """Render the command of ``action`` and check that everything it names exists."""
        tool = TOOL_OF_ACTION[action]
        blocking = blocking_findings(self._settings_findings, action)
        if blocking:
            return PreparedCommand(action, tool, None, blocking)
        template = self._settings.commands[action]
        used = placeholders_in(template.args)
        values = self.values(action, extra)
        findings: list[Finding] = []
        if "exe" in used and values["exe"] is None:
            findings.append(missing_executable(self.lookup(tool)))
        if check_paths:
            for name in _FILE_PLACEHOLDERS:
                value = values.get(name)
                if name in used and isinstance(value, str) and not os.path.isfile(value):
                    findings.append(self._missing_path(action, name, value, "file"))
            if "script_dir" in used and not self._settings.script_dir.is_dir():
                findings.append(
                    self._missing_path(
                        action, "script_dir", str(self._settings.script_dir), "folder"
                    )
                )
        rendered = render_command(template, values)
        findings.extend(
            f
            for f in rendered.findings
            if not (f.code == "DVR-TPL-UNRESOLVED" and "{exe}" in f.message)
        )
        if rendered.argv and "exe" not in placeholders_in(template.args[:1]):
            program = rendered.argv[0]
            if not os.path.isfile(program) and shutil.which(program) is None:
                findings.append(
                    Finding(
                        "DVR-EXE-MISSING",
                        Severity.ERROR,
                        f"The program {program!r} of [davinci.commands] {action} in "
                        "porttool.toml does not exist. Correct the first argument of that "
                        "template, or write {exe} there and set the path in "
                        f"[davinci.executables] {tool.lower()}.",
                        object_path=f"davinci.commands.{action}",
                    )
                )
        if any(finding.severity is Severity.ERROR for finding in findings):
            return PreparedCommand(action, tool, None, tuple(findings))
        return PreparedCommand(action, tool, rendered, tuple(findings))

    def _missing_path(self, action: str, name: str, value: str, kind: str) -> Finding:
        hints = {
            "dcf": " Correct dcf_file in the [paths] table of porttool.toml.",
            "dpa": " Correct dpa_file in the [paths] table of porttool.toml.",
            "script_dir": " Correct [davinci] script_dir in porttool.toml, or remove the line "
            "to use the folder that ships with PortTool.",
        }
        return Finding(
            "DVR-PATH-MISSING",
            Severity.ERROR,
            f"The action {action} needs the {kind} {value} ({{{name}}}), which does not "
            f"exist. {TOOL_OF_ACTION[action]} was not started.{hints.get(name, '')}",
            object_path=f"davinci.commands.{action}",
            file=value,
        )

    def rendered_commands(self) -> tuple[PreparedCommand, ...]:
        """Every action's command line with example values, for ``show-commands``. Runs nothing."""
        modules = tuple(self._config.generate_modules)
        examples: dict[str, Value] = {
            "arxml": "<delta.arxml>",
            "modules": modules,
            "modules_csv": ",".join(modules),
            "swcs": ("<SwcName>",),
            "swcs_csv": "<SwcName>",
            "request_json": "<script_request.json>",
            "result_json": "<script_result.json>",
        }
        return tuple(self.prepare(action, examples, check_paths=False) for action in ACTIONS)

    # ------------------------------------------------------------- running

    def _new_log_file(self, action: str, tool: str) -> Path:
        folder = self.log_dir()
        folder.mkdir(parents=True, exist_ok=True)
        logs = sorted(folder.glob("*_p*_*.log"), key=lambda path: path.name)
        for old in logs[: max(0, len(logs) - self._settings.max_tool_logs + 1)]:
            try:
                old.unlink()
            except OSError:
                continue  # in use or protected: it is tried again at the next run
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        return folder / f"{stamp}_p{os.getpid()}_{next(_sequence):04d}_{action}_{tool}.log"

    def _environment(self, extra: Mapping[str, str]) -> dict[str, str]:
        env = dict(os.environ)
        env.update(self._settings.env)
        env["PORTTOOL_LOG_DIR"] = str(self.log_dir())
        env.update(extra)
        return env

    async def run_action(
        self,
        action: str,
        extra: Mapping[str, Value] | None,
        ctx: JobContext,
        env: Mapping[str, str] | None = None,
    ) -> ToolResult:
        """Render, run and judge one action. Every adapter ends here."""
        ctx.check_cancelled()
        prepared = self.prepare(action, extra)
        tool = prepared.tool
        if prepared.rendered is None or not prepared.runnable:
            return ToolResult(tool, False, None, 0.0, None, prepared.findings)
        try:
            log_file = await ctx.run_blocking(lambda: self._new_log_file(action, tool))
        except OSError as error:
            example = toml_string(r"C:\some\writable\folder")
            finding = Finding(
                "DVR-LOG-DIR",
                Severity.ERROR,
                f"The log folder {self.log_dir()} cannot be used ({type(error).__name__}: "
                f"{error}). {tool} was not started. Fix: in porttool.toml, table [davinci], "
                f"add the line: log_dir = {example}",
                object_path="davinci.log_dir",
            )
            return ToolResult(tool, False, None, 0.0, None, (*prepared.findings, finding))
        template = self._settings.commands[action]
        request = ProcessRequest(
            label=f"{tool} (action {action})",
            argv=prepared.rendered.argv,
            cwd=self._config.repo_root,
            env=self._environment(env or {}),
            timeout_s=timeout_for(action, self._config.timeouts),
            log_file=log_file,
            rules=self._settings.results,
            encoding=self._settings.encoding,
            max_kept_lines=self._settings.max_kept_output_lines,
            leftover_children=self._settings.leftover_children,
            header=(
                f"tool: {tool}; action: {action}; command template: {template.label}",
                f"started (UTC): {datetime.now(UTC).isoformat(timespec='seconds')}",
            ),
        )
        started_wall = time.time()
        outcome = await run_process(request, ctx)
        self._last[action] = outcome
        reports = await ctx.run_blocking(lambda: self._scan_reports(started_wall))
        return self._judge(action, tool, outcome, reports, prepared.findings)

    # ------------------------------------------------------------- judging

    def _scan_reports(self, started_wall: float) -> list[tuple[Path, Matches, Matches, Matches]]:
        """Scan the report files of ``[davinci.results] report_globs`` written by this run."""
        scanned: list[tuple[Path, Matches, Matches, Matches]] = []
        seen: set[Path] = set()
        for pattern in self._settings.results.report_globs:
            text = pattern.replace("{log_dir}", glob.escape(str(self.log_dir())))
            if not os.path.isabs(text):
                text = os.path.join(glob.escape(str(self._config.repo_root)), text)
            for name in sorted(glob.glob(text, recursive=True)):
                path = Path(name)
                if path in seen or len(scanned) >= MAX_REPORT_FILES:
                    continue
                try:
                    if not path.is_file() or path.stat().st_mtime < started_wall - 2.0:
                        continue  # not written by this run
                    seen.add(path)
                    scanned.append((path, *scan_report(path, self._settings)))
                except OSError:
                    continue  # locked or gone: the tool's own output was judged already
        return scanned

    def _line_findings(
        self, tool: str, matches: Matches, kind: str, severity: Severity, file: Path, where: str
    ) -> list[Finding]:
        code = f"DVR-RUN-{kind.upper()}-LINE"
        findings = [
            Finding(
                code,
                severity,
                f"{tool}, line {line.line_no} of {where}: {line.text}",
                file=str(file),
            )
            for line in matches.lines[:MAX_MATCHES]
        ]
        if matches.total > len(findings):
            findings.append(
                Finding(
                    code,
                    severity,
                    f"{tool}: {matches.total - len(findings)} more {kind} line(s) are in "
                    f"{where} {file}.",
                    file=str(file),
                )
            )
        return findings

    def _judge(
        self,
        action: str,
        tool: str,
        outcome: ProcessOutcome,
        reports: Sequence[tuple[Path, Matches, Matches, Matches]],
        earlier: Sequence[Finding],
    ) -> ToolResult:
        log = outcome.log_file
        findings: list[Finding] = list(earlier)
        template = self._settings.commands[action]
        allowed = self._settings.results.exit_codes_for(action)
        ok = True
        if outcome.spawn_error is not None:
            ok = False
            findings.append(
                Finding(
                    "DVR-RUN-SPAWN",
                    Severity.ERROR,
                    f"{tool} could not be started ({outcome.spawn_error}). Command: "
                    f"{toml_list(outcome.argv)}. Check the path of the executable and that "
                    f"it may be run. Log: {log}",
                    file=str(log),
                )
            )
        elif outcome.exit_code not in allowed:
            ok = False
            code = outcome.exit_code if outcome.exit_code is not None else -1
            crash = (
                " A code this large usually means the tool crashed."
                if code < 0 or code >= 0x80000000
                else ""
            )
            wanted = toml_list([str(c) for c in (*allowed, code)]).replace("'", "")
            findings.append(
                Finding(
                    "DVR-RUN-EXIT",
                    Severity.ERROR,
                    f"{tool} ended with exit code {code}{_hex_code(code)}, which is not a "
                    f"success code of the action {action} (success: "
                    f"{', '.join(str(c) for c in allowed)}).{crash} Log: {log}. If this code "
                    "means success for this tool: in porttool.toml, table "
                    f"[davinci.results.success_exit_codes], add the line: {action} = {wanted}",
                    file=str(log),
                )
            )
        if outcome.errors.total:
            ok = False
        findings += self._line_findings(
            tool, outcome.errors, "error", Severity.ERROR, log, "the log"
        )
        findings += self._line_findings(
            tool, outcome.warnings, "warning", Severity.WARNING, log, "the log"
        )
        for path, errors, warnings, licenses in reports:
            if licenses.total:
                first = licenses.lines[0]
                raise LicenseError(
                    f"{tool} (action {action}) reported a license problem in its report "
                    f"file: {first.text!r} (line {first.line_no} of {path}).",
                    detail=str(path),
                )
            if errors.total:
                ok = False
            findings += self._line_findings(
                tool, errors, "error", Severity.ERROR, path, "the report"
            )
            findings += self._line_findings(
                tool, warnings, "warning", Severity.WARNING, path, "the report"
            )
        if outcome.leftover:
            names = ", ".join(str(info) for info in outcome.leftover)
            done = (
                "They were stopped."
                if outcome.leftover_killed
                else 'They were left running ([davinci] leftover_children = "report").'
            )
            findings.append(
                Finding(
                    "DVR-RUN-ORPHAN",
                    Severity.WARNING,
                    f"{tool} ended, but process(es) it had started were still running: "
                    f"{names}. {done} Log: {log}",
                    file=str(log),
                )
            )
        if outcome.survivors:
            ok = False
            names = ", ".join(str(info) for info in outcome.survivors)
            findings.append(
                Finding(
                    "DVR-RUN-KILL-FAILED",
                    Severity.ERROR,
                    f"These processes started by {tool} could not be stopped: {names}. End "
                    f"them in the Task Manager before the next DaVinci step. Log: {log}",
                    file=str(log),
                )
            )
        if not template.verified:
            findings.append(
                Finding(
                    "DVR-TPL-UNVERIFIED",
                    Severity.WARNING,
                    f"The command line of the action {action} is a built-in guess "
                    f"(UNVERIFIED): {toml_list(template.args)}. If {tool} "
                    + ("failed, a wrong flag is the likely reason. " if not ok else "worked, ")
                    + "confirm or correct it in porttool.toml, table [davinci.commands] "
                    "(see `porttool davinci doctor`).",
                    object_path=f"davinci.commands.{action}",
                )
            )
        return ToolResult(tool, ok, outcome.exit_code, outcome.elapsed_s, str(log), tuple(findings))

    # ------------------------------------------------------------- adapters

    def _nothing_given(self, action: str, what: str) -> ToolResult:
        tool = TOOL_OF_ACTION[action]
        finding = Finding(
            "DVR-ARG-EMPTY",
            Severity.ERROR,
            f"The action {action} was asked for without any {what}. {tool} was not started.",
            object_path=f"davinci.commands.{action}",
        )
        return ToolResult(tool, False, None, 0.0, None, (finding,))

    async def check_workspace(self, ctx: JobContext) -> ToolResult:
        return await self.run_action("check", None, ctx)

    async def import_arxml(self, arxml: Path, ctx: JobContext) -> ToolResult:
        return await self.run_action("import", {"arxml": str(arxml)}, ctx)

    async def generate_contract_headers(
        self, swc_names: Sequence[str], ctx: JobContext
    ) -> ToolResult:
        names = tuple(swc_names)
        if not names:
            return self._nothing_given("contract_headers", "SWC name")
        extra: dict[str, Value] = {"swcs": names, "swcs_csv": ",".join(names)}
        return await self.run_action("contract_headers", extra, ctx)

    async def run_script_task(
        self, request_json: Path, result_json: Path, ctx: JobContext
    ) -> ToolResult:
        extra: dict[str, Value] = {
            "request_json": str(request_json),
            "result_json": str(result_json),
        }
        env = {
            "PORTTOOL_REQUEST_JSON": str(request_json),
            "PORTTOOL_RESULT_JSON": str(result_json),
        }
        return await self.run_action("script_task", extra, ctx, env)

    async def validate_project(self, ctx: JobContext) -> ToolResult:
        return await self.run_action("validate", None, ctx)

    async def generate(self, modules: Sequence[str], ctx: JobContext) -> ToolResult:
        names = tuple(modules) or tuple(self._config.generate_modules)
        if not names:
            return self._nothing_given("generate", "module")
        extra: dict[str, Value] = {"modules": names, "modules_csv": ",".join(names)}
        return await self.run_action("generate", extra, ctx)


__all__ = ["EXECUTABLE_KEYS", "DaVinciToolRunner", "PreparedCommand", "timeout_for"]
