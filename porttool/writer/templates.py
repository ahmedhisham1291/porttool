"""The XML of every new element, as small trees that ``xmltext`` renders.

PENDING-DISCOVERY: each function here is one assumption. The element names, their
order and the DEST values follow the AUTOSAR 4.x schema. Which optional children
DaVinci Developer writes (and so what a file saved by it looks like) is not known:
the golden diffs of ``porttool discover`` decide. Options that are likely to differ
are settings (``WriterSettings``).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence

from porttool.core.models import (
    DataTypeCategory,
    DataTypeLevel,
    InterfaceKind,
    Port,
    PortDirection,
    PortInterface,
)
from porttool.core.operations import AddDataType, AddInterface
from porttool.writer.settings import WriterSettings
from porttool.writer.xmltext import Node, element, named

Uuid = Callable[[], "str | None"]

PORT_TAGS: Mapping[PortDirection, str] = {
    PortDirection.P: "P-PORT-PROTOTYPE",
    PortDirection.R: "R-PORT-PROTOTYPE",
    PortDirection.PR: "PR-PORT-PROTOTYPE",
}
_TREF_TAGS: Mapping[PortDirection, str] = {
    PortDirection.P: "PROVIDED-INTERFACE-TREF",
    PortDirection.R: "REQUIRED-INTERFACE-TREF",
    PortDirection.PR: "PROVIDED-REQUIRED-INTERFACE-TREF",
}
INTERFACE_TAGS: Mapping[InterfaceKind, str] = {
    InterfaceKind.SENDER_RECEIVER: "SENDER-RECEIVER-INTERFACE",
    InterfaceKind.CLIENT_SERVER: "CLIENT-SERVER-INTERFACE",
    InterfaceKind.MODE_SWITCH: "MODE-SWITCH-INTERFACE",
    InterfaceKind.PARAMETER: "PARAMETER-INTERFACE",
    InterfaceKind.NV_DATA: "NV-DATA-INTERFACE",
    InterfaceKind.TRIGGER: "TRIGGER-INTERFACE",
}
IMPLEMENTATION_TYPE = "IMPLEMENTATION-DATA-TYPE"
BASE_TYPE = "SW-BASE-TYPE"
DATA_ELEMENT = "VARIABLE-DATA-PROTOTYPE"
OPERATION = "CLIENT-SERVER-OPERATION"


def application_tag(category: DataTypeCategory) -> str:
    if category is DataTypeCategory.ARRAY:
        return "APPLICATION-ARRAY-DATA-TYPE"
    if category is DataTypeCategory.RECORD:
        return "APPLICATION-RECORD-DATA-TYPE"
    return "APPLICATION-PRIMITIVE-DATA-TYPE"


def data_type_tag(level: DataTypeLevel, category: DataTypeCategory) -> str:
    if level is DataTypeLevel.IMPLEMENTATION:
        return IMPLEMENTATION_TYPE
    return application_tag(category)


def number(value: float) -> str:
    """Whole numbers without a decimal point, everything else as Python prints it."""
    return str(int(value)) if value == int(value) else repr(value)


def ref(tag: str, dest: str, path: str) -> Node:
    return element(tag, text=path, attrs=(("DEST", dest),))


def _props(*children: Node | None) -> Node | None:
    kept = [child for child in children if child is not None]
    if not kept:
        return None
    return element(
        "SW-DATA-DEF-PROPS",
        element("SW-DATA-DEF-PROPS-VARIANTS", element("SW-DATA-DEF-PROPS-CONDITIONAL", *kept)),
    )


# --------------------------------------------------------------------------- values

_NUMBER = re.compile(
    r"[+-]?(?:0[xX][0-9a-fA-F]+|0[bB][01]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?|INF|NaN)\Z"
)


def _split(text: str) -> list[str]:
    """Split ``a, [b, c], d`` at the commas that are not inside brackets or braces."""
    parts: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(text):
        if char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[start:index].strip())
            start = index + 1
    last = text[start:].strip()
    if last or parts:
        parts.append(last)
    return parts


def value_spec(text: str, is_constant: Callable[[str], bool]) -> Node:
    """Value specification for the textual form the reader produces.

    A number gives NUMERICAL-VALUE-SPECIFICATION, the path of a constant gives
    CONSTANT-REFERENCE, ``[a, b]`` an array, ``{a, b}`` a record, anything else
    TEXT-VALUE-SPECIFICATION.
    """
    text = text.strip()
    if len(text) >= 2 and text[0] == "[" and text[-1] == "]":
        items = [value_spec(item, is_constant) for item in _split(text[1:-1])]
        return element("ARRAY-VALUE-SPECIFICATION", element("ELEMENTS", *items))
    if len(text) >= 2 and text[0] == "{" and text[-1] == "}":
        items = [value_spec(item, is_constant) for item in _split(text[1:-1])]
        return element("RECORD-VALUE-SPECIFICATION", element("FIELDS", *items))
    if text.startswith("/") and is_constant(text):
        return element("CONSTANT-REFERENCE", ref("CONSTANT-REF", "CONSTANT-SPECIFICATION", text))
    if _NUMBER.match(text):
        return element("NUMERICAL-VALUE-SPECIFICATION", element("VALUE", text=text))
    return element("TEXT-VALUE-SPECIFICATION", element("VALUE", text=text))


# --------------------------------------------------------------------------- ports


def port(
    name: str,
    direction: PortDirection,
    interface: PortInterface,
    inits: Mapping[str, str | None],
    settings: WriterSettings,
    uuid: Uuid,
    is_constant: Callable[[str], bool],
) -> Node:
    provides = direction in (PortDirection.P, PortDirection.PR)
    requires = direction in (PortDirection.R, PortDirection.PR)
    provided: list[Node] = []
    required: list[Node] = []

    def init_of(element_name: str) -> Node | None:
        value = inits.get(element_name)
        if value is None:
            return None
        return element("INIT-VALUE", value_spec(value, is_constant))

    if interface.kind is InterfaceKind.SENDER_RECEIVER:
        for data in interface.elements:
            if not settings.com_spec_for_every_element and inits.get(data.name) is None:
                continue
            target = f"{interface.path}/{data.name}"
            if provides and data.is_queued:
                provided.append(
                    element("QUEUED-SENDER-COM-SPEC", ref("DATA-ELEMENT-REF", DATA_ELEMENT, target))
                )
            elif provides:
                provided.append(
                    element(
                        "NONQUEUED-SENDER-COM-SPEC",
                        ref("DATA-ELEMENT-REF", DATA_ELEMENT, target),
                        init_of(data.name),
                    )
                )
            if requires and data.is_queued:
                required.append(
                    element(
                        "QUEUED-RECEIVER-COM-SPEC",
                        ref("DATA-ELEMENT-REF", DATA_ELEMENT, target),
                        element("QUEUE-LENGTH", text=str(settings.queue_length)),
                    )
                )
            elif requires:
                defaults: Sequence[Node] = ()
                if settings.receiver_com_spec_defaults:
                    defaults = (
                        element("ALIVE-TIMEOUT", text="0"),
                        element("ENABLE-UPDATE", text="false"),
                        element("HANDLE-NEVER-RECEIVED", text="false"),
                        element("HANDLE-TIMEOUT-TYPE", text="NONE"),
                    )
                required.append(
                    element(
                        "NONQUEUED-RECEIVER-COM-SPEC",
                        ref("DATA-ELEMENT-REF", DATA_ELEMENT, target),
                        *defaults,
                        init_of(data.name),
                    )
                )
    elif interface.kind is InterfaceKind.CLIENT_SERVER:
        for operation in interface.operations:
            target = f"{interface.path}/{operation.name}"
            if provides:
                provided.append(
                    element(
                        "SERVER-COM-SPEC",
                        ref("OPERATION-REF", OPERATION, target),
                        element("QUEUE-LENGTH", text=str(settings.queue_length)),
                    )
                )
            if requires and settings.client_com_spec:
                required.append(element("CLIENT-COM-SPEC", ref("OPERATION-REF", OPERATION, target)))
    return named(
        PORT_TAGS[direction],
        name,
        element("PROVIDED-COM-SPECS", *provided) if provided else None,
        element("REQUIRED-COM-SPECS", *required) if required else None,
        ref(_TREF_TAGS[direction], INTERFACE_TAGS[interface.kind], interface.path),
        uuid=uuid(),
    )


def connector(
    name: str,
    provider_prototype: str,
    provider: Port,
    requester_prototype: str,
    requester: Port,
    uuid: Uuid,
) -> Node:
    return named(
        "ASSEMBLY-SW-CONNECTOR",
        name,
        element(
            "PROVIDER-IREF",
            ref("CONTEXT-COMPONENT-REF", "SW-COMPONENT-PROTOTYPE", provider_prototype),
            ref("TARGET-P-PORT-REF", PORT_TAGS[provider.direction], provider.path),
        ),
        element(
            "REQUESTER-IREF",
            ref("CONTEXT-COMPONENT-REF", "SW-COMPONENT-PROTOTYPE", requester_prototype),
            ref("TARGET-R-PORT-REF", PORT_TAGS[requester.direction], requester.path),
        ),
        uuid=uuid(),
    )


# --------------------------------------------------------------------------- behavior


def runnable(name: str, symbol: str, uuid: Uuid) -> Node:
    return named(
        "RUNNABLE-ENTITY",
        name,
        element("MINIMUM-START-INTERVAL", text="0"),
        element("CAN-BE-INVOKED-CONCURRENTLY", text="false"),
        element("SYMBOL", text=symbol),
        uuid=uuid(),
    )


def variable_access(name: str, accessed: Port, element_path: str, uuid: Uuid) -> Node:
    return named(
        "VARIABLE-ACCESS",
        name,
        element(
            "ACCESSED-VARIABLE",
            element(
                "AUTOSAR-VARIABLE-IREF",
                ref("PORT-PROTOTYPE-REF", PORT_TAGS[accessed.direction], accessed.path),
                ref("TARGET-DATA-PROTOTYPE-REF", DATA_ELEMENT, element_path),
            ),
        ),
        uuid=uuid(),
    )


def server_call_point(
    tag: str, name: str, client: Port, operation_path: str, timeout: str, uuid: Uuid
) -> Node:
    return named(
        tag,
        name,
        element(
            "OPERATION-IREF",
            ref("CONTEXT-R-PORT-REF", PORT_TAGS[client.direction], client.path),
            ref("TARGET-REQUIRED-OPERATION-REF", OPERATION, operation_path),
        ),
        element("TIMEOUT", text=timeout),
        uuid=uuid(),
    )


def event(tag: str, name: str, runnable_path: str, uuid: Uuid, *details: Node | None) -> Node:
    return named(
        tag,
        name,
        ref("START-ON-EVENT-REF", "RUNNABLE-ENTITY", runnable_path),
        *details,
        uuid=uuid(),
    )


def data_iref(receiver: Port, element_path: str) -> Node:
    return element(
        "DATA-IREF",
        ref("CONTEXT-R-PORT-REF", PORT_TAGS[receiver.direction], receiver.path),
        ref("TARGET-DATA-ELEMENT-REF", DATA_ELEMENT, element_path),
    )


def operation_iref(server: Port, operation_path: str) -> Node:
    return element(
        "OPERATION-IREF",
        ref("CONTEXT-P-PORT-REF", PORT_TAGS[server.direction], server.path),
        ref("TARGET-PROVIDED-OPERATION-REF", OPERATION, operation_path),
    )


# --------------------------------------------------------------------------- interfaces


def interface(
    op: AddInterface, type_dest: Callable[[str], str], settings: WriterSettings, uuid: Uuid
) -> Node:
    """SENDER-RECEIVER-INTERFACE or CLIENT-SERVER-INTERFACE. The caller refuses other kinds."""
    top = uuid()
    if op.kind is InterfaceKind.SENDER_RECEIVER:
        elements = [
            named(
                DATA_ELEMENT,
                item.name,
                _props(
                    element("SW-IMPL-POLICY", text="QUEUED")
                    if item.is_queued
                    else element("SW-IMPL-POLICY", text="STANDARD")
                    if settings.standard_impl_policy
                    else None
                ),
                ref("TYPE-TREF", type_dest(item.type_path), item.type_path),
                uuid=uuid(),
            )
            for item in op.elements
        ]
        return named(
            INTERFACE_TAGS[op.kind],
            op.name,
            element("IS-SERVICE", text="false"),
            element("DATA-ELEMENTS", *elements) if elements else None,
            uuid=top,
        )
    operations = [
        named(
            OPERATION,
            operation.name,
            element(
                "ARGUMENTS",
                *(
                    named(
                        "ARGUMENT-DATA-PROTOTYPE",
                        argument.name,
                        ref("TYPE-TREF", type_dest(argument.type_path), argument.type_path),
                        element("DIRECTION", text=argument.direction.value),
                        uuid=uuid(),
                    )
                    for argument in operation.arguments
                ),
            )
            if operation.arguments
            else None,
            uuid=uuid(),
        )
        for operation in op.operations
    ]
    return named(
        INTERFACE_TAGS[op.kind],
        op.name,
        element("IS-SERVICE", text="false"),
        element("OPERATIONS", *operations) if operations else None,
        uuid=top,
    )


# --------------------------------------------------------------------------- data types


def _limit(tag: str, value: float) -> Node:
    return element(tag, text=number(value), attrs=(("INTERVAL-TYPE", "CLOSED"),))


def compu_method(name: str, op: AddDataType, unit_path: str | None, uuid: Uuid) -> Node:
    """TEXTTABLE for an enumeration, LINEAR otherwise."""
    if op.category is DataTypeCategory.ENUMERATION:
        category = "TEXTTABLE"
        scales = [
            element(
                "COMPU-SCALE",
                _limit("LOWER-LIMIT", value),
                _limit("UPPER-LIMIT", value),
                element("COMPU-CONST", element("VT", text=text)),
            )
            for value, text in op.enum_values
        ]
    else:
        category = "LINEAR"
        scales = [
            element(
                "COMPU-SCALE",
                element(
                    "COMPU-RATIONAL-COEFFS",
                    element(
                        "COMPU-NUMERATOR",
                        element("V", text=number(op.offset if op.offset is not None else 0.0)),
                        element("V", text=number(op.factor if op.factor is not None else 1.0)),
                    ),
                    element("COMPU-DENOMINATOR", element("V", text="1")),
                ),
            )
        ]
    return named(
        "COMPU-METHOD",
        name,
        element("CATEGORY", text=category),
        ref("UNIT-REF", "UNIT", unit_path) if unit_path else None,
        element("COMPU-INTERNAL-TO-PHYS", element("COMPU-SCALES", *scales)),
        uuid=uuid(),
    )


def data_constr(name: str, op: AddDataType, uuid: Uuid) -> Node:
    """Internal limits for an implementation type, physical limits for an application type."""
    holder = "INTERNAL-CONSTRS" if op.level is DataTypeLevel.IMPLEMENTATION else "PHYS-CONSTRS"
    return named(
        "DATA-CONSTR",
        name,
        element(
            "DATA-CONSTR-RULES",
            element(
                "DATA-CONSTR-RULE",
                element(
                    holder,
                    _limit("LOWER-LIMIT", op.lower_limit) if op.lower_limit is not None else None,
                    _limit("UPPER-LIMIT", op.upper_limit) if op.upper_limit is not None else None,
                ),
            ),
        ),
        uuid=uuid(),
    )


def _sub_element(name: str, type_path: str, dest: str, size: int | None, uuid: Uuid) -> Node:
    is_base = dest == BASE_TYPE
    return named(
        "IMPLEMENTATION-DATA-TYPE-ELEMENT",
        name,
        element("CATEGORY", text="VALUE" if is_base else "TYPE_REFERENCE"),
        element("ARRAY-SIZE", text=str(size)) if size is not None else None,
        element("ARRAY-SIZE-SEMANTICS", text="FIXED-SIZE") if size is not None else None,
        _props(
            ref("BASE-TYPE-REF", BASE_TYPE, type_path)
            if is_base
            else ref("IMPLEMENTATION-DATA-TYPE-REF", IMPLEMENTATION_TYPE, type_path)
        ),
        uuid=uuid(),
    )


def data_type(
    op: AddDataType,
    type_dest: Callable[[str], str],
    compu_path: str | None,
    constr_path: str | None,
    unit_path: str | None,
    settings: WriterSettings,
    uuid: Uuid,
) -> Node:
    """The data type element. The caller refuses categories that are not handled here."""
    top = uuid()
    compu = ref("COMPU-METHOD-REF", "COMPU-METHOD", compu_path) if compu_path else None
    constr = ref("DATA-CONSTR-REF", "DATA-CONSTR", constr_path) if constr_path else None
    if op.level is DataTypeLevel.APPLICATION:
        tag = application_tag(op.category)
        if op.category is DataTypeCategory.ARRAY:
            assert op.element_type_path is not None
            return named(
                tag,
                op.name,
                element("CATEGORY", text="ARRAY"),
                named(
                    "ELEMENT",
                    settings.array_element_name,
                    ref("TYPE-TREF", type_dest(op.element_type_path), op.element_type_path),
                    element("ARRAY-SIZE-SEMANTICS", text="FIXED-SIZE"),
                    element("MAX-NUMBER-OF-ELEMENTS", text=str(op.array_size or 0)),
                    uuid=uuid(),
                ),
                uuid=top,
            )
        if op.category is DataTypeCategory.RECORD:
            return named(
                tag,
                op.name,
                element("CATEGORY", text="STRUCTURE"),
                element(
                    "ELEMENTS",
                    *(
                        named(
                            "APPLICATION-RECORD-ELEMENT",
                            item.name,
                            ref("TYPE-TREF", type_dest(item.type_path), item.type_path),
                            uuid=uuid(),
                        )
                        for item in op.record_elements
                    ),
                ),
                uuid=top,
            )
        category = "BOOLEAN" if op.category is DataTypeCategory.BOOLEAN else "VALUE"
        unit = ref("UNIT-REF", "UNIT", unit_path) if unit_path and compu is None else None
        return named(
            tag,
            op.name,
            element("CATEGORY", text=category),
            _props(compu, constr, unit),
            uuid=top,
        )
    if op.category is DataTypeCategory.ARRAY:
        assert op.element_type_path is not None
        return named(
            IMPLEMENTATION_TYPE,
            op.name,
            element("CATEGORY", text="ARRAY"),
            element(
                "SUB-ELEMENTS",
                _sub_element(
                    settings.array_element_name,
                    op.element_type_path,
                    type_dest(op.element_type_path),
                    op.array_size or 0,
                    uuid,
                ),
            ),
            uuid=top,
        )
    if op.category is DataTypeCategory.RECORD:
        return named(
            IMPLEMENTATION_TYPE,
            op.name,
            element("CATEGORY", text="STRUCTURE"),
            element(
                "SUB-ELEMENTS",
                *(
                    _sub_element(item.name, item.type_path, type_dest(item.type_path), None, uuid)
                    for item in op.record_elements
                ),
            ),
            uuid=top,
        )
    assert op.base_type_path is not None
    if op.category is DataTypeCategory.TYPE_REFERENCE:
        return named(
            IMPLEMENTATION_TYPE,
            op.name,
            element("CATEGORY", text="TYPE_REFERENCE"),
            _props(
                compu,
                constr,
                ref("IMPLEMENTATION-DATA-TYPE-REF", IMPLEMENTATION_TYPE, op.base_type_path),
            ),
            uuid=top,
        )
    return named(
        IMPLEMENTATION_TYPE,
        op.name,
        element("CATEGORY", text="VALUE"),
        _props(ref("BASE-TYPE-REF", BASE_TYPE, op.base_type_path), compu, constr),
        uuid=top,
    )


def data_type_map(application_path: str, application_dest: str, implementation_path: str) -> Node:
    return element(
        "DATA-TYPE-MAP",
        ref("APPLICATION-DATA-TYPE-REF", application_dest, application_path),
        ref("IMPLEMENTATION-DATA-TYPE-REF", IMPLEMENTATION_TYPE, implementation_path),
    )


def package(name: str) -> Node:
    return named("AR-PACKAGE", name)
