"""Child element order of the AUTOSAR elements the writer inserts containers into.

PENDING-DISCOVERY: these tables were written from the AUTOSAR 4.x schema as remembered
(base class first, then the attributes of each class in the order of the XSD). The
XSD is not available in the build sandbox. Confirm every table against the XSD of the
project (``xsd_file`` in porttool.toml makes the writer validate each touched file).

A tag that is missing here is not an error: the writer then reports ``WRT-ORDER-001``
when the position of a new container depends on it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

_IDENTIFIABLE = (
    "SHORT-NAME",
    "SHORT-NAME-FRAGMENTS",
    "LONG-NAME",
    "DESC",
    "CATEGORY",
    "ADMIN-DATA",
    "INTRODUCTION",
    "ANNOTATIONS",
)
_SW_COMPONENT_TYPE = (
    *_IDENTIFIABLE,
    "CONSISTENCY-NEEDSS",
    "PORTS",
    "PORT-GROUPS",
    "SWC-MAPPING-CONSTRAINT-REFS",
    "SW-COMPONENT-DOCUMENTATIONS",
    "UNIT-GROUP-REFS",
)
_ATOMIC = (*_SW_COMPONENT_TYPE, "INTERNAL-BEHAVIORS", "SYMBOL-PROPS")
_PORT_INTERFACE = (*_IDENTIFIABLE, "IS-SERVICE", "NAMESPACES", "SERVICE-KIND")

ORDER: Mapping[str, Sequence[str]] = {
    "AUTOSAR": ("FILE-INFO-COMMENT", "ADMIN-DATA", "INTRODUCTION", "AR-PACKAGES"),
    "AR-PACKAGE": (*_IDENTIFIABLE, "REFERENCE-BASES", "ELEMENTS", "AR-PACKAGES"),
    "APPLICATION-SW-COMPONENT-TYPE": _ATOMIC,
    "SERVICE-SW-COMPONENT-TYPE": _ATOMIC,
    "SERVICE-PROXY-SW-COMPONENT-TYPE": _ATOMIC,
    "SENSOR-ACTUATOR-SW-COMPONENT-TYPE": (*_ATOMIC, "SENSOR-ACTUATOR-REF"),
    "COMPLEX-DEVICE-DRIVER-SW-COMPONENT-TYPE": (*_ATOMIC, "HARDWARE-ELEMENT-REFS"),
    "ECU-ABSTRACTION-SW-COMPONENT-TYPE": (*_ATOMIC, "HARDWARE-ELEMENT-REFS"),
    "NV-BLOCK-SW-COMPONENT-TYPE": (*_ATOMIC, "BULK-NV-DATA-DESCRIPTORS", "NV-BLOCK-DESCRIPTORS"),
    "PARAMETER-SW-COMPONENT-TYPE": (
        *_SW_COMPONENT_TYPE,
        "CONSTANT-MAPPING-REFS",
        "DATA-TYPE-MAPPING-REFS",
        "INSTANTIATION-DATA-DEF-PROPSS",
    ),
    "COMPOSITION-SW-COMPONENT-TYPE": (
        *_SW_COMPONENT_TYPE,
        "COMPONENTS",
        "CONNECTORS",
        "CONSTANT-VALUE-MAPPING-REFS",
        "DATA-TYPE-MAPPING-REFS",
        "INSTANTIATION-RTE-EVENT-PROPSS",
    ),
    "SWC-INTERNAL-BEHAVIOR": (
        *_IDENTIFIABLE,
        "CONSTANT-MEMORYS",
        "CONSTANT-VALUE-MAPPING-REFS",
        "DATA-TYPE-MAPPING-REFS",
        "EXCLUSIVE-AREAS",
        "EXCLUSIVE-AREA-NESTING-ORDERS",
        "STATIC-MEMORYS",
        "AR-TYPED-PER-INSTANCE-MEMORYS",
        "EVENTS",
        "EXCLUSIVE-AREA-POLICYS",
        "EXPLICIT-INTER-RUNNABLE-VARIABLES",
        "HANDLE-TERMINATION-AND-RESTART",
        "IMPLICIT-INTER-RUNNABLE-VARIABLES",
        "INCLUDED-DATA-TYPE-SETS",
        "INCLUDED-MODE-DECLARATION-GROUP-SETS",
        "INSTANTIATION-DATA-DEF-PROPSS",
        "PER-INSTANCE-MEMORYS",
        "PER-INSTANCE-PARAMETERS",
        "PORT-API-OPTIONS",
        "RUNNABLES",
        "SERVICE-DEPENDENCYS",
        "SHARED-PARAMETERS",
        "SUPPORTS-MULTIPLE-INSTANTIATION",
        "VARIATION-POINT-PROXYS",
        "VARIATION-POINT",
    ),
    "RUNNABLE-ENTITY": (
        *_IDENTIFIABLE,
        "ACTIVATION-REASONS",
        "CAN-ENTER-EXCLUSIVE-AREA-REFS",
        "EXCLUSIVE-AREA-NESTING-ORDER-REFS",
        "MINIMUM-START-INTERVAL",
        "REENTRANCY-LEVEL",
        "RUNS-INSIDE-EXCLUSIVE-AREA-REFS",
        "SW-ADDR-METHOD-REF",
        "ARGUMENTS",
        "ASYNCHRONOUS-SERVER-CALL-RESULT-POINTS",
        "CAN-BE-INVOKED-CONCURRENTLY",
        "DATA-READ-ACCESSS",
        "DATA-RECEIVE-POINT-BY-ARGUMENTS",
        "DATA-RECEIVE-POINT-BY-VALUES",
        "DATA-SEND-POINTS",
        "DATA-WRITE-ACCESSS",
        "EXTERNAL-TRIGGERING-POINTS",
        "INTERNAL-TRIGGERING-POINTS",
        "MODE-ACCESS-POINTS",
        "MODE-SWITCH-POINTS",
        "PARAMETER-ACCESSS",
        "READ-LOCAL-VARIABLES",
        "SERVER-CALL-POINTS",
        "SYMBOL",
        "WAIT-POINTS",
        "WRITTEN-LOCAL-VARIABLES",
        "VARIATION-POINT",
    ),
    "SENDER-RECEIVER-INTERFACE": (
        *_PORT_INTERFACE,
        "DATA-ELEMENTS",
        "INVALIDATION-POLICYS",
        "META-DATA-ITEM-SETS",
    ),
    "CLIENT-SERVER-INTERFACE": (*_PORT_INTERFACE, "OPERATIONS", "POSSIBLE-ERRORS"),
    "DATA-TYPE-MAPPING-SET": (*_IDENTIFIABLE, "DATA-TYPE-MAPS", "MODE-REQUEST-TYPE-MAPS"),
}


def position(parent_tag: str, new_tag: str, child_tags: Sequence[str]) -> tuple[int, bool]:
    """Where a new ``new_tag`` child goes among ``child_tags``.

    Returns (index to insert before, certain). ``certain`` is False when the schema
    order of a sibling is not known, so the caller can warn.
    """
    table = ORDER.get(parent_tag)
    if table is None or new_tag not in table:
        return len(child_tags), not child_tags
    rank = {tag: index for index, tag in enumerate(table)}
    mine = rank[new_tag]
    certain = True
    index = len(child_tags)
    for at, tag in reversed(list(enumerate(child_tags))):
        other = rank.get(tag)
        if other is None:
            certain = False
        elif other > mine:
            index = at
        else:
            break
    return index, certain
