"""Content of the dropdown lists on the hidden Lists sheet."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Final

from porttool.core.errors import WorkbookError
from porttool.core.models import (
    AccessKind,
    ArgumentDirection,
    DataTypeCategory,
    DataTypeLevel,
    InterfaceKind,
    Model,
    PortDirection,
    TriggerKind,
)
from porttool.core.sheets import Action
from porttool.workbook.layout import dropdown_names
from porttool.workbook.rows import NO, YES, Labels

#: Lists with a closed set of values. Excel refuses any other entry in their columns.
#: Every other list comes from the model and only warns, because a request may name an
#: object that another row of the same workbook creates.
ENUM_LISTS: Final[frozenset[str]] = frozenset(
    {
        "actions",
        "yes_no",
        "data_type_levels",
        "data_type_categories",
        "interface_kinds",
        "argument_directions",
        "port_directions",
        "access_kinds",
        "trigger_kinds",
    }
)


def _enum_values(enum: type[StrEnum]) -> tuple[str, ...]:
    return tuple(member.value for member in enum)


def _sorted_unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(values), key=lambda text: (text.casefold(), text)))


def build_lists(model: Model, labels: Labels) -> dict[str, tuple[str, ...]]:
    """Return the entries of every list named by ``Column.dropdown``, keyed by list name.

    Raises ``WorkbookError`` when the layout contract names a list this package cannot
    fill: that is a contract change, not a problem of the user's data.
    """
    lists: dict[str, tuple[str, ...]] = {
        "actions": _enum_values(Action),
        "yes_no": (YES, NO),
        "data_type_levels": _enum_values(DataTypeLevel),
        "data_type_categories": _enum_values(DataTypeCategory),
        "interface_kinds": _enum_values(InterfaceKind),
        "argument_directions": _enum_values(ArgumentDirection),
        "port_directions": _enum_values(PortDirection),
        "access_kinds": _enum_values(AccessKind),
        "trigger_kinds": _enum_values(TriggerKind),
        "swcs": _sorted_unique(labels.components.values()),
        "prototypes": _sorted_unique(p.name for p in model.prototypes.values()),
        "interfaces": _sorted_unique(labels.interfaces.values()),
        "data_types": _sorted_unique(labels.data_types.values()),
        "base_types": _sorted_unique(labels.base_types.values()),
        "units": _sorted_unique(labels.units.values()),
        "tasks": _sorted_unique(model.tasks),
        "compositions": _sorted_unique(labels.compositions.values()),
    }
    unknown = sorted(dropdown_names() - lists.keys())
    if unknown:
        raise WorkbookError(
            "The workbook layout names dropdown lists the exporter cannot fill.",
            detail=", ".join(unknown),
        )
    return {name: lists[name] for name in sorted(lists)}
