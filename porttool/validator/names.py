"""Name rules: AUTOSAR short-name syntax, length, project pattern, C keywords."""

from __future__ import annotations

import re
from enum import StrEnum

from porttool.core.config import NamingRules
from porttool.validator import codes

_SHORT_NAME = re.compile(r"[a-zA-Z][a-zA-Z0-9_]*")

#: Keywords of C90, C99 and C11. Names are compared case-sensitively, as the compiler does.
C_KEYWORDS: frozenset[str] = frozenset(
    {
        "auto",
        "break",
        "case",
        "char",
        "const",
        "continue",
        "default",
        "do",
        "double",
        "else",
        "enum",
        "extern",
        "float",
        "for",
        "goto",
        "if",
        "inline",
        "int",
        "long",
        "register",
        "restrict",
        "return",
        "short",
        "signed",
        "sizeof",
        "static",
        "struct",
        "switch",
        "typedef",
        "union",
        "unsigned",
        "void",
        "volatile",
        "while",
    }
)


class NameKind(StrEnum):
    """What a name belongs to. The first five have a project pattern in ``NamingRules``."""

    PORT = "port"
    INTERFACE = "interface"
    DATA_TYPE = "data type"
    RUNNABLE = "runnable"
    CONNECTOR = "connector"
    ELEMENT = "element"
    OPERATION = "operation"
    ARGUMENT = "argument"
    RECORD_ELEMENT = "record element"
    ENUM_LABEL = "enumeration label"


_PATTERN_ATTRIBUTE: dict[NameKind, str] = {
    NameKind.PORT: "port",
    NameKind.INTERFACE: "interface",
    NameKind.DATA_TYPE: "data_type",
    NameKind.RUNNABLE: "runnable",
    NameKind.CONNECTOR: "connector",
}


class NameChecker:
    """Checks names against ``NamingRules``. Compiles the project patterns once."""

    def __init__(self, naming: NamingRules) -> None:
        self.max_length: int = naming.max_length
        self._patterns: dict[NameKind, re.Pattern[str]] = {}
        self._broken: dict[NameKind, str] = {}
        #: ``(code, message)`` pairs about the configuration itself, reported once per run.
        self.config_problems: list[tuple[str, str]] = []
        for kind, attribute in _PATTERN_ATTRIBUTE.items():
            source: str = getattr(naming, attribute)
            if not source:
                continue
            try:
                self._patterns[kind] = re.compile(source)
            except re.error as error:
                self._broken[kind] = source
                self.config_problems.append(
                    (
                        codes.CFG_NAME_REGEX,
                        f"The naming rule for {kind.value} names ('{source}') is not a valid "
                        f"regular expression ({error}). No {kind.value} name can be checked "
                        "until it is fixed.",
                    )
                )

    def check(self, name: str, kind: NameKind) -> list[tuple[str, str]]:
        """Return ``(code, message)`` for every rule ``name`` breaks; empty when it is fine."""
        problems: list[tuple[str, str]] = []
        label = f"{kind.value.capitalize()} name '{name}'"
        if not _SHORT_NAME.fullmatch(name):
            problems.append(
                (
                    codes.NAME_SYNTAX,
                    f"{label} is not a valid AUTOSAR short name: it must start with a letter "
                    "and contain only letters, digits and underscores.",
                )
            )
        if len(name) > self.max_length:
            problems.append(
                (
                    codes.NAME_TOO_LONG,
                    f"{label} has {len(name)} characters; the maximum is {self.max_length}.",
                )
            )
        if name in C_KEYWORDS:
            problems.append((codes.NAME_C_KEYWORD, f"{label} is a C keyword."))
        pattern = self._patterns.get(kind)
        if pattern is not None and not pattern.fullmatch(name):
            problems.append(
                (
                    codes.NAME_PATTERN,
                    f"{label} does not match the project naming rule '{pattern.pattern}'.",
                )
            )
        elif kind in self._broken:
            problems.append(
                (
                    codes.NAME_PATTERN,
                    f"{label} cannot be checked: the project naming rule "
                    f"'{self._broken[kind]}' is not a valid regular expression.",
                )
            )
        return problems
