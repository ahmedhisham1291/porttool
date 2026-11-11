"""Parsers for the text formats of workbook cells. Pure functions, no model access.

Every parser returns ``None`` (or a ``(None, reason)`` pair) for text it cannot read; it
never raises. The reason is a short phrase the caller puts into the Finding message.
"""

from __future__ import annotations

import math
import re
from enum import StrEnum
from typing import TypeVar

from porttool.core.models import (
    AccessKind,
    ArgumentDirection,
    DataTypeCategory,
    DataTypeLevel,
    InterfaceKind,
    PortDirection,
    TriggerKind,
)

_E = TypeVar("_E", bound=StrEnum)

_SEPARATORS = re.compile(r"[\s_\-]+")
_INT = re.compile(r"[+-]?(?:0[xX][0-9a-fA-F]+|[0-9]+)")
_TRUE = frozenset({"y", "yes", "true", "1", "x"})
_FALSE = frozenset({"", "n", "no", "false", "0"})

#: The literal that means "explicitly no init value".
NONE_LITERAL = "none"


def token(text: str) -> str:
    """Lower-case ``text`` and fold spaces, hyphens and underscores into one space."""
    return _SEPARATORS.sub(" ", text.strip().lower())


def _lookup(enum_type: type[_E], extra: dict[str, _E] | None = None) -> dict[str, _E]:
    table: dict[str, _E] = {}
    for member in enum_type:
        table[token(member.value)] = member
        table[token(member.name)] = member
    if extra:
        table.update(extra)
    return table


_PORT_DIRECTIONS = _lookup(PortDirection)
_INTERFACE_KINDS = _lookup(InterfaceKind)
_ACCESS_KINDS = _lookup(AccessKind)
_TRIGGER_KINDS = _lookup(TriggerKind)
_LEVELS = _lookup(DataTypeLevel)
_CATEGORIES = _lookup(DataTypeCategory)
_ARGUMENT_DIRECTIONS = _lookup(ArgumentDirection)


def parse_port_direction(text: str) -> PortDirection | None:
    return _PORT_DIRECTIONS.get(token(text))


def parse_interface_kind(text: str) -> InterfaceKind | None:
    return _INTERFACE_KINDS.get(token(text))


def parse_access_kind(text: str) -> AccessKind | None:
    return _ACCESS_KINDS.get(token(text))


def parse_trigger_kind(text: str) -> TriggerKind | None:
    return _TRIGGER_KINDS.get(token(text))


def parse_level(text: str) -> DataTypeLevel | None:
    return _LEVELS.get(token(text))


def parse_category(text: str) -> DataTypeCategory | None:
    return _CATEGORIES.get(token(text))


def parse_argument_direction(text: str) -> ArgumentDirection | None:
    return _ARGUMENT_DIRECTIONS.get(token(text))


def parse_flag(text: str) -> bool | None:
    """Y/N flag. Empty means N. ``None`` for anything that is not a yes or a no."""
    folded = text.strip().lower()
    if folded in _TRUE:
        return True
    if folded in _FALSE:
        return False
    return None


def parse_int(text: str) -> int | None:
    """Whole number: decimal, hexadecimal (0x..), or a float text with no fraction ("5.0")."""
    stripped = text.strip()
    if _INT.fullmatch(stripped):
        return int(stripped, 0) if "x" in stripped.lower() else int(stripped, 10)
    number = _plain_float(stripped)
    if number is not None and number == math.floor(number) and abs(number) < 2**63:
        return int(number)
    return None


def _plain_float(text: str) -> float | None:
    if not text or text.lower().lstrip("+-") in {"nan", "inf", "infinity"}:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def parse_number(text: str) -> float | None:
    """Finite number with a dot as decimal separator; hexadecimal whole numbers are accepted."""
    stripped = text.strip()
    if _INT.fullmatch(stripped):
        whole = parse_int(stripped)
        return float(whole) if whole is not None else None
    return _plain_float(stripped)


def split_list(text: str) -> list[str]:
    """Split a ``;`` separated cell into its non-empty, stripped parts."""
    return [part.strip() for part in text.split(";") if part.strip()]


def parse_enum_values(text: str) -> tuple[tuple[tuple[int, str], ...] | None, str]:
    """Parse ``0=OFF;1=ON``. Returns ``(pairs, "")`` or ``(None, reason)``."""
    pairs: list[tuple[int, str]] = []
    for part in split_list(text):
        value_text, sep, label = part.partition("=")
        value = parse_int(value_text)
        label = label.strip()
        if not sep or value is None or not label:
            return None, f"'{part}' is not of the form value=LABEL"
        pairs.append((value, label))
    return tuple(pairs), ""


def parse_record_elements(text: str) -> tuple[tuple[tuple[str, str], ...] | None, str]:
    """Parse ``name:Type;name2:Type2``. Returns ``(pairs, "")`` or ``(None, reason)``."""
    pairs: list[tuple[str, str]] = []
    for part in split_list(text):
        name, sep, type_text = part.partition(":")
        name = name.strip()
        type_text = type_text.strip()
        if not sep or not name or not type_text:
            return None, f"'{part}' is not of the form name:Type"
        pairs.append((name, type_text))
    return tuple(pairs), ""


def is_none_literal(text: str) -> bool:
    return text.strip().lower() == NONE_LITERAL


def parse_init_cell(text: str) -> tuple[str | None, tuple[tuple[str, str], ...] | None, str]:
    """Parse an init value cell.

    Returns ``(single, pairs, reason)``:

    * ``("5", None, "")`` for one bare value (also the bare literal ``none``),
    * ``(None, (("A", "1"), ("B", "none")), "")`` for ``A=1;B=none``,
    * ``(None, None, "")`` for an empty cell,
    * ``(None, None, reason)`` for text that is neither.
    """
    stripped = text.strip()
    if not stripped:
        return None, None, ""
    if "=" not in stripped:
        if ";" in stripped:
            return None, None, "several values need the form Element=value;Element2=value2"
        return stripped, None, ""
    pairs: list[tuple[str, str]] = []
    for part in split_list(stripped):
        element, sep, value = part.partition("=")
        element = element.strip()
        value = value.strip()
        if not sep or not element or not value:
            return None, None, f"'{part}' is not of the form Element=value"
        pairs.append((element, value))
    return None, tuple(pairs), ""
