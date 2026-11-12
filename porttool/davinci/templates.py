"""Command-line templates of the Vector tools and how they are rendered.

A template is a list of arguments. Each argument may contain ``{placeholder}`` names.
Nothing here knows a real flag of a real tool: the flags live in the templates, and the
templates live in ``[davinci.commands]`` of porttool.toml (see ``settings.py`` for the
built-in guesses, every one of them UNVERIFIED).

Rules:

* A scalar placeholder is replaced inside its argument: ``--log={log_dir}/x.txt``.
* An argument that contains a list placeholder (``{modules}``, ``{swcs}``) is repeated
  once per item, so ``{modules}`` becomes several arguments and ``--module={modules}``
  becomes ``--module=Rte --module=Os``. An empty list gives no argument.
* ``{{`` and ``}}`` give a literal brace.
* A placeholder that is unknown, or has no value for this action, makes the command
  unusable: it is reported as a Finding and the tool is never started.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from porttool.core.results import Finding, Severity

#: Placeholders that stand for one text.
SCALAR_PLACEHOLDERS: tuple[str, ...] = (
    "exe",
    "dcf",
    "dpa",
    "arxml",
    "modules_csv",
    "swcs_csv",
    "script_dir",
    "script_task",
    "request_json",
    "result_json",
    "log_dir",
)
#: Placeholders that stand for several arguments.
LIST_PLACEHOLDERS: tuple[str, ...] = ("modules", "swcs")
ALL_PLACEHOLDERS: tuple[str, ...] = (*SCALAR_PLACEHOLDERS, *LIST_PLACEHOLDERS)

#: What the owner has to do when a placeholder has no value.
PLACEHOLDER_HINTS: Mapping[str, str] = {
    "exe": "the executable of the tool was not found (see the DVR-EXE-MISSING finding)",
    "dcf": "set dcf_file in the [paths] table of porttool.toml",
    "dpa": "set dpa_file in the [paths] table of porttool.toml",
    "arxml": "this action is not given an ARXML file",
    "modules": "this action is not given a module list",
    "modules_csv": "this action is not given a module list",
    "swcs": "this action is not given SWC names",
    "swcs_csv": "this action is not given SWC names",
    "request_json": "this action is not given a script request file",
    "result_json": "this action is not given a script result file",
}

_TOKEN_RE = re.compile(r"\{\{|\}\}|\{([^{}\s]*)\}")

Value = str | Sequence[str] | None


def _finding(code: str, message: str, action: str, severity: Severity = Severity.ERROR) -> Finding:
    return Finding(code, severity, message, object_path=f"davinci.commands.{action}")


@dataclass(frozen=True, slots=True)
class CommandTemplate:
    """The argument-list template of one action."""

    action: str
    args: tuple[str, ...]
    #: True when the owner wrote the template in porttool.toml. A built-in default is a
    #: guess and stays UNVERIFIED until the owner confirms it there.
    verified: bool
    usable: bool = True

    @property
    def label(self) -> str:
        return "set in porttool.toml" if self.verified else "UNVERIFIED built-in guess"


@dataclass(frozen=True, slots=True)
class RenderedCommand:
    """A template with its placeholders filled in. ``argv`` is empty when it has errors."""

    action: str
    argv: tuple[str, ...]
    verified: bool
    findings: tuple[Finding, ...] = ()

    @property
    def ok(self) -> bool:
        return bool(self.argv) and not any(f.severity is Severity.ERROR for f in self.findings)

    def display(self) -> str:
        """The command line as one text, quoted the way Windows shows it."""
        return subprocess.list2cmdline(list(self.argv))


def placeholders_in(args: Sequence[str]) -> tuple[str, ...]:
    """Every placeholder name used by a template, in order of first use."""
    names: list[str] = []
    for arg in args:
        for match in _TOKEN_RE.finditer(arg):
            name = match.group(1)
            if name is not None and name not in names:
                names.append(name)
    return tuple(names)


def toml_list(items: Sequence[str]) -> str:
    """A TOML array of strings, written so that Windows paths need no escaping."""
    return "[" + ", ".join(toml_string(item) for item in items) + "]"


def toml_string(text: str) -> str:
    """A TOML string: a literal ``'...'`` when possible, else a basic ``"..."`` string."""
    if "'" not in text and all(ch >= " " and ch != "\x7f" for ch in text):
        return f"'{text}'"
    out = ['"']
    for ch in text:
        if ch in ('"', "\\"):
            out.append("\\" + ch)
        elif ch < " " or ch == "\x7f":
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def parse_template(
    action: str, raw: object, *, verified: bool
) -> tuple[CommandTemplate, tuple[Finding, ...]]:
    """Check the shape of a template. A broken template is returned as not usable."""
    broken = CommandTemplate(action, (), verified, usable=False)
    example = 'check = ["{exe}", "-w", "{dcf}"]'
    if isinstance(raw, str) or not isinstance(raw, Sequence):
        message = (
            f"[davinci.commands] {action} must be a list of arguments, one text per "
            f"argument, for example: {example}. A single text is not split into "
            "arguments, because paths contain spaces."
        )
        return broken, (_finding("DVR-TPL-NOT-LIST", message, action),)
    if not raw:
        message = f"[davinci.commands] {action} is an empty list. Example: {example}"
        return broken, (_finding("DVR-TPL-EMPTY", message, action),)
    if not all(isinstance(item, str) for item in raw):
        message = (
            f"[davinci.commands] {action}: every argument must be a text in quotes "
            f"(numbers too, for example '3'). Example: {example}"
        )
        return broken, (_finding("DVR-TPL-NOT-LIST", message, action),)
    args = tuple(str(item) for item in raw)
    findings: list[Finding] = []
    unknown = [name for name in placeholders_in(args) if name not in ALL_PLACEHOLDERS]
    if unknown:
        names = ", ".join("{" + name + "}" for name in unknown)
        known = " ".join("{" + name + "}" for name in ALL_PLACEHOLDERS)
        findings.append(
            _finding(
                "DVR-TPL-UNKNOWN",
                f"[davinci.commands] {action} uses the unknown placeholder {names}. "
                f"Known placeholders: {known}. Write {{{{ and }}}} for a literal brace.",
                action,
            )
        )
    for arg in args:
        lists = [name for name in placeholders_in([arg]) if name in LIST_PLACEHOLDERS]
        if len(lists) > 1:
            findings.append(
                _finding(
                    "DVR-TPL-LIST-MIX",
                    f"[davinci.commands] {action}: the argument {arg!r} uses two list "
                    "placeholders. Use at most one of {modules} and {swcs} per argument.",
                    action,
                )
            )
    if findings:
        return broken, tuple(findings)
    return CommandTemplate(action, args, verified), ()


def _fill(arg: str, values: Mapping[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        token = match.group(0)
        if token == "{{":
            return "{"
        if token == "}}":
            return "}"
        return values[match.group(1)]

    return _TOKEN_RE.sub(replace, arg)


def render_command(template: CommandTemplate, values: Mapping[str, Value]) -> RenderedCommand:
    """Fill the placeholders of ``template``.

    ``values`` maps a placeholder name to its text, to a list of texts (list
    placeholders), or to None when this action has no value for it.
    """
    action = template.action
    if not template.usable:
        message = (
            f"The command template of the action {action} in porttool.toml is not usable. "
            "Run `porttool davinci doctor` to see what is wrong with it."
        )
        return RenderedCommand(
            action, (), template.verified, (_finding("DVR-TPL-INVALID", message, action),)
        )
    findings: list[Finding] = []
    for name in placeholders_in(template.args):
        if name not in ALL_PLACEHOLDERS:
            findings.append(
                _finding(
                    "DVR-TPL-UNKNOWN",
                    f"The command of the action {action} uses the unknown placeholder "
                    f"{{{name}}}. The tool was not started.",
                    action,
                )
            )
        elif values.get(name) is None:
            hint = PLACEHOLDER_HINTS.get(name, "it has no value")
            findings.append(
                _finding(
                    "DVR-TPL-UNRESOLVED",
                    f"The command of the action {action} needs {{{name}}}, which cannot be "
                    f"filled in: {hint}. The tool was not started.",
                    action,
                )
            )
    if findings:
        return RenderedCommand(action, (), template.verified, tuple(findings))

    scalars: dict[str, str] = {}
    lists: dict[str, tuple[str, ...]] = {}
    for name, value in values.items():
        if value is None:
            continue
        if isinstance(value, str):
            scalars[name] = value
        else:
            lists[name] = tuple(value)
    argv: list[str] = []
    for arg in template.args:
        used = placeholders_in([arg])
        list_names = [name for name in used if name in LIST_PLACEHOLDERS]
        if not list_names:
            argv.append(_fill(arg, scalars))
            continue
        name = list_names[0]
        items = lists.get(name)
        if items is None:  # a list placeholder that was given one text
            items = (scalars[name],)
        for item in items:
            argv.append(_fill(arg, {**scalars, name: item}))
    if not argv or not argv[0]:
        message = f"The command of the action {action} has no program to start."
        return RenderedCommand(
            action, (), template.verified, (_finding("DVR-TPL-EMPTY", message, action),)
        )
    return RenderedCommand(action, tuple(argv), template.verified)
