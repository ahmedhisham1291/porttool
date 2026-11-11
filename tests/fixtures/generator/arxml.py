"""AUTOSAR 4.x XML for the planned objects, written with plain string templates.

Everything here follows the AUTOSAR standard schema (namespace
``http://autosar.org/schema/r4.0``): element names, element order and reference DEST
values. Nothing DaVinci-specific lives in this module; see ``davinci_seam.py``.

The XML was written from the standard by hand and is checked for well-formedness and
reference integrity by the tests. It was NOT validated against the AUTOSAR XSD (the
schema file is not available in the build sandbox); set PORTTOOL_AUTOSAR_XSD to run
that check.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from porttool.core.models import AccessKind, ComponentKind, TriggerKind
from tests.fixtures.generator.plan import (
    ARRAY,
    COMPOSITION,
    COMPOSITION_NAME,
    ENUM,
    RECORD,
    AccessPlan,
    BaseTypePlan,
    DataTypePlan,
    EventPlan,
    InterfacePlan,
    Plan,
    PortPlan,
    RunnablePlan,
    SwcPlan,
    UnitPlan,
)

NAMESPACE = "http://autosar.org/schema/r4.0"
#: PENDING-DISCOVERY: the schema version of the real project (4.2.2 is an assumption).
SCHEMA_FILE = "AUTOSAR_4-2-2.xsd"
_HEADER = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    f'<AUTOSAR xmlns="{NAMESPACE}" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
    f'xsi:schemaLocation="{NAMESPACE} {SCHEMA_FILE}">\n'
)

COMPONENT_TAGS = {
    ComponentKind.APPLICATION: "APPLICATION-SW-COMPONENT-TYPE",
    ComponentKind.SENSOR_ACTUATOR: "SENSOR-ACTUATOR-SW-COMPONENT-TYPE",
    ComponentKind.COMPLEX_DEVICE_DRIVER: "COMPLEX-DEVICE-DRIVER-SW-COMPONENT-TYPE",
}
#: Container element and VARIABLE-ACCESS role per access kind, in schema order.
_ACCESS_GROUPS = (
    (AccessKind.IMPLICIT_READ, "DATA-READ-ACCESSS"),
    (AccessKind.EXPLICIT_READ, "DATA-RECEIVE-POINT-BY-ARGUMENTS"),
    (AccessKind.EXPLICIT_SEND, "DATA-SEND-POINTS"),
    (AccessKind.IMPLICIT_WRITE, "DATA-WRITE-ACCESSS"),
)
EVENT_TAGS = {
    TriggerKind.INIT: "INIT-EVENT",
    TriggerKind.PERIODIC: "TIMING-EVENT",
    TriggerKind.DATA_RECEIVED: "DATA-RECEIVED-EVENT",
    TriggerKind.OPERATION_INVOKED: "OPERATION-INVOKED-EVENT",
}


def number(value: float) -> str:
    """Whole numbers without a decimal point, everything else as Python prints it."""
    return str(int(value)) if value == int(value) else repr(value)


def escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _indent(text: str, levels: int) -> str:
    pad = "  " * levels
    return pad + text[:-1].replace("\n", "\n" + pad) + "\n"


class _Package:
    def __init__(self, name: str) -> None:
        self.name = name
        self.elements: list[str] = []
        self.children: dict[str, _Package] = {}

    def render(self, level: int, out: list[str]) -> None:
        pad = "  " * level
        out.append(f"{pad}<AR-PACKAGE>\n{pad}  <SHORT-NAME>{self.name}</SHORT-NAME>\n")
        if self.elements:
            out.append(f"{pad}  <ELEMENTS>\n")
            out.extend(_indent(element, level + 2) for element in self.elements)
            out.append(f"{pad}  </ELEMENTS>\n")
        if self.children:
            out.append(f"{pad}  <AR-PACKAGES>\n")
            for child in self.children.values():
                child.render(level + 2, out)
            out.append(f"{pad}  </AR-PACKAGES>\n")
        out.append(f"{pad}</AR-PACKAGE>\n")


def document(contents: Iterable[tuple[str, str]]) -> str:
    """One ARXML file. ``contents``: (package path, element XML starting at column 0)."""
    roots: dict[str, _Package] = {}
    for package_path, element in contents:
        level = roots
        node: _Package | None = None
        for name in package_path.strip("/").split("/"):
            node = level.setdefault(name, _Package(name))
            level = node.children
        assert node is not None
        node.elements.append(element)
    out = [_HEADER, "  <AR-PACKAGES>\n"]
    for root in roots.values():
        root.render(2, out)
    out.append("  </AR-PACKAGES>\n</AUTOSAR>\n")
    return "".join(out)


class Renderer:
    """Element XML for planned objects. ``uuids=False`` leaves the UUID attributes out."""

    def __init__(self, uuids: bool = True) -> None:
        self._uuids = uuids

    def _u(self, uuid: str) -> str:
        return f' UUID="{uuid}"' if self._uuids else ""

    # ---- data types

    def base_type(self, base: BaseTypePlan) -> str:
        return (
            f"<SW-BASE-TYPE{self._u(base.uuid)}>\n"
            f"  <SHORT-NAME>{base.name}</SHORT-NAME>\n"
            "  <CATEGORY>FIXED_LENGTH</CATEGORY>\n"
            f"  <BASE-TYPE-SIZE>{base.size_bits}</BASE-TYPE-SIZE>\n"
            f"  <BASE-TYPE-ENCODING>{base.encoding}</BASE-TYPE-ENCODING>\n"
            f"  <NATIVE-DECLARATION>{base.native}</NATIVE-DECLARATION>\n"
            "</SW-BASE-TYPE>\n"
        )

    def unit(self, unit: UnitPlan) -> str:
        return (
            f"<UNIT{self._u(unit.uuid)}>\n"
            f"  <SHORT-NAME>{unit.name}</SHORT-NAME>\n"
            f"  <DISPLAY-NAME>{escape(unit.display)}</DISPLAY-NAME>\n"
            "</UNIT>\n"
        )

    @staticmethod
    def _props(lines: Sequence[str], pad: str) -> str:
        body = "".join(f"{pad}      {line}\n" for line in lines)
        return (
            f"{pad}<SW-DATA-DEF-PROPS>\n"
            f"{pad}  <SW-DATA-DEF-PROPS-VARIANTS>\n"
            f"{pad}    <SW-DATA-DEF-PROPS-CONDITIONAL>\n"
            f"{body}"
            f"{pad}    </SW-DATA-DEF-PROPS-CONDITIONAL>\n"
            f"{pad}  </SW-DATA-DEF-PROPS-VARIANTS>\n"
            f"{pad}</SW-DATA-DEF-PROPS>\n"
        )

    def _sub_element(self, name: str, target: DataTypePlan, array_size: int | None) -> str:
        size = ""
        if array_size is not None:
            size = (
                f"      <ARRAY-SIZE>{array_size}</ARRAY-SIZE>\n"
                "      <ARRAY-SIZE-SEMANTICS>FIXED-SIZE</ARRAY-SIZE-SEMANTICS>\n"
            )
        reference = (
            '<IMPLEMENTATION-DATA-TYPE-REF DEST="IMPLEMENTATION-DATA-TYPE">'
            f"{target.path}</IMPLEMENTATION-DATA-TYPE-REF>"
        )
        return (
            "    <IMPLEMENTATION-DATA-TYPE-ELEMENT>\n"
            f"      <SHORT-NAME>{name}</SHORT-NAME>\n"
            "      <CATEGORY>TYPE_REFERENCE</CATEGORY>\n"
            f"{size}"
            f"{self._props([reference], '      ')}"
            "    </IMPLEMENTATION-DATA-TYPE-ELEMENT>\n"
        )

    def data_type(self, data_type: DataTypePlan) -> str:
        head = f"<IMPLEMENTATION-DATA-TYPE{self._u(data_type.uuid)}>\n"
        head += f"  <SHORT-NAME>{data_type.name}</SHORT-NAME>\n"
        if data_type.category == ARRAY:
            assert data_type.element is not None
            body = (
                "  <CATEGORY>ARRAY</CATEGORY>\n  <SUB-ELEMENTS>\n"
                + self._sub_element("Element", data_type.element, data_type.array_size)
                + "  </SUB-ELEMENTS>\n"
            )
        elif data_type.category == RECORD:
            body = (
                "  <CATEGORY>STRUCTURE</CATEGORY>\n  <SUB-ELEMENTS>\n"
                + "".join(
                    self._sub_element(name, target, None) for name, target in data_type.fields
                )
                + "  </SUB-ELEMENTS>\n"
            )
        else:
            assert data_type.base_type is not None
            references = [
                f'<BASE-TYPE-REF DEST="SW-BASE-TYPE">{data_type.base_type.path}</BASE-TYPE-REF>',
                f'<COMPU-METHOD-REF DEST="COMPU-METHOD">{data_type.compu_path}</COMPU-METHOD-REF>',
                f'<DATA-CONSTR-REF DEST="DATA-CONSTR">{data_type.constr_path}</DATA-CONSTR-REF>',
            ]
            body = "  <CATEGORY>VALUE</CATEGORY>\n" + self._props(references, "  ")
        return head + body + "</IMPLEMENTATION-DATA-TYPE>\n"

    def compu_method(self, data_type: DataTypePlan) -> str:
        head = f"<COMPU-METHOD{self._u(data_type.compu_uuid)}>\n"
        head += f"  <SHORT-NAME>CM_{data_type.name}</SHORT-NAME>\n"
        if data_type.category == ENUM:
            scales = "".join(
                "      <COMPU-SCALE>\n"
                f'        <LOWER-LIMIT INTERVAL-TYPE="CLOSED">{value}</LOWER-LIMIT>\n'
                f'        <UPPER-LIMIT INTERVAL-TYPE="CLOSED">{value}</UPPER-LIMIT>\n'
                "        <COMPU-CONST>\n"
                f"          <VT>{name}</VT>\n"
                "        </COMPU-CONST>\n"
                "      </COMPU-SCALE>\n"
                for value, name in data_type.enum_values
            )
            head += "  <CATEGORY>TEXTTABLE</CATEGORY>\n"
        else:
            assert data_type.unit is not None
            scales = (
                "      <COMPU-SCALE>\n"
                "        <COMPU-RATIONAL-COEFFS>\n"
                "          <COMPU-NUMERATOR>\n"
                f"            <V>{number(data_type.offset)}</V>\n"
                f"            <V>{number(data_type.factor)}</V>\n"
                "          </COMPU-NUMERATOR>\n"
                "          <COMPU-DENOMINATOR>\n"
                "            <V>1</V>\n"
                "          </COMPU-DENOMINATOR>\n"
                "        </COMPU-RATIONAL-COEFFS>\n"
                "      </COMPU-SCALE>\n"
            )
            head += "  <CATEGORY>LINEAR</CATEGORY>\n"
            head += f'  <UNIT-REF DEST="UNIT">{data_type.unit.path}</UNIT-REF>\n'
        return (
            head
            + "  <COMPU-INTERNAL-TO-PHYS>\n    <COMPU-SCALES>\n"
            + scales
            + "    </COMPU-SCALES>\n  </COMPU-INTERNAL-TO-PHYS>\n</COMPU-METHOD>\n"
        )

    def constraint(self, data_type: DataTypePlan) -> str:
        return (
            f"<DATA-CONSTR{self._u(data_type.constr_uuid)}>\n"
            f"  <SHORT-NAME>DC_{data_type.name}</SHORT-NAME>\n"
            "  <DATA-CONSTR-RULES>\n"
            "    <DATA-CONSTR-RULE>\n"
            "      <INTERNAL-CONSTRS>\n"
            f'        <LOWER-LIMIT INTERVAL-TYPE="CLOSED">{number(data_type.lower)}</LOWER-LIMIT>\n'
            f'        <UPPER-LIMIT INTERVAL-TYPE="CLOSED">{number(data_type.upper)}</UPPER-LIMIT>\n'
            "      </INTERNAL-CONSTRS>\n"
            "    </DATA-CONSTR-RULE>\n"
            "  </DATA-CONSTR-RULES>\n"
            "</DATA-CONSTR>\n"
        )

    # ---- port interfaces

    def interface(self, interface: InterfacePlan) -> str:
        if not interface.client_server:
            elements = "".join(
                f"    <VARIABLE-DATA-PROTOTYPE{self._u(element.uuid)}>\n"
                f"      <SHORT-NAME>{element.name}</SHORT-NAME>\n"
                f"{self._props(['<SW-IMPL-POLICY>STANDARD</SW-IMPL-POLICY>'], '      ')}"
                f'      <TYPE-TREF DEST="IMPLEMENTATION-DATA-TYPE">{element.data_type.path}'
                "</TYPE-TREF>\n"
                "    </VARIABLE-DATA-PROTOTYPE>\n"
                for element in interface.elements
            )
            return (
                f"<SENDER-RECEIVER-INTERFACE{self._u(interface.uuid)}>\n"
                f"  <SHORT-NAME>{interface.name}</SHORT-NAME>\n"
                "  <IS-SERVICE>false</IS-SERVICE>\n"
                f"  <DATA-ELEMENTS>\n{elements}  </DATA-ELEMENTS>\n"
                "</SENDER-RECEIVER-INTERFACE>\n"
            )
        operations = ""
        for operation in interface.operations:
            arguments = "".join(
                "        <ARGUMENT-DATA-PROTOTYPE>\n"
                f"          <SHORT-NAME>{argument.name}</SHORT-NAME>\n"
                f'          <TYPE-TREF DEST="IMPLEMENTATION-DATA-TYPE">{argument.data_type.path}'
                "</TYPE-TREF>\n"
                f"          <DIRECTION>{argument.direction}</DIRECTION>\n"
                "        </ARGUMENT-DATA-PROTOTYPE>\n"
                for argument in operation.arguments
            )
            errors = "".join(
                f'        <POSSIBLE-ERROR-REF DEST="APPLICATION-ERROR">{interface.path}/{name}'
                "</POSSIBLE-ERROR-REF>\n"
                for name in operation.possible_errors
            )
            operations += (
                f"    <CLIENT-SERVER-OPERATION{self._u(operation.uuid)}>\n"
                f"      <SHORT-NAME>{operation.name}</SHORT-NAME>\n"
                f"      <ARGUMENTS>\n{arguments}      </ARGUMENTS>\n"
            )
            if errors:
                operations += f"      <POSSIBLE-ERROR-REFS>\n{errors}      </POSSIBLE-ERROR-REFS>\n"
            operations += "    </CLIENT-SERVER-OPERATION>\n"
        possible = "".join(
            "    <APPLICATION-ERROR>\n"
            f"      <SHORT-NAME>{name}</SHORT-NAME>\n"
            f"      <ERROR-CODE>{code}</ERROR-CODE>\n"
            "    </APPLICATION-ERROR>\n"
            for name, code in interface.errors
        )
        text = (
            f"<CLIENT-SERVER-INTERFACE{self._u(interface.uuid)}>\n"
            f"  <SHORT-NAME>{interface.name}</SHORT-NAME>\n"
            "  <IS-SERVICE>false</IS-SERVICE>\n"
            f"  <OPERATIONS>\n{operations}  </OPERATIONS>\n"
        )
        if possible:
            text += f"  <POSSIBLE-ERRORS>\n{possible}  </POSSIBLE-ERRORS>\n"
        return text + "</CLIENT-SERVER-INTERFACE>\n"

    # ---- software components

    @staticmethod
    def _init_value(value: str | None, pad: str) -> str:
        if value is None:
            return ""
        return (
            f"{pad}<INIT-VALUE>\n"
            f"{pad}  <NUMERICAL-VALUE-SPECIFICATION>\n"
            f"{pad}    <VALUE>{value}</VALUE>\n"
            f"{pad}  </NUMERICAL-VALUE-SPECIFICATION>\n"
            f"{pad}</INIT-VALUE>\n"
        )

    def port(self, port: PortPlan) -> str:
        interface = port.interface
        assert interface is not None
        tag = "P-PORT-PROTOTYPE" if port.provider else "R-PORT-PROTOTYPE"
        side = "PROVIDED" if port.provider else "REQUIRED"
        kind = "CLIENT-SERVER-INTERFACE" if interface.client_server else "SENDER-RECEIVER-INTERFACE"
        specs = ""
        if interface.client_server and port.provider:
            specs = "".join(
                "    <SERVER-COM-SPEC>\n"
                f'      <OPERATION-REF DEST="CLIENT-SERVER-OPERATION">{interface.path}/'
                f"{operation.name}</OPERATION-REF>\n"
                "      <QUEUE-LENGTH>1</QUEUE-LENGTH>\n"
                "    </SERVER-COM-SPEC>\n"
                for operation in interface.operations
            )
        elif not interface.client_server:
            for element in interface.elements:
                reference = (
                    f'      <DATA-ELEMENT-REF DEST="VARIABLE-DATA-PROTOTYPE">{interface.path}/'
                    f"{element.name}</DATA-ELEMENT-REF>\n"
                )
                init = self._init_value(element.init_value, "      ")
                if port.provider:
                    specs += (
                        f"    <NONQUEUED-SENDER-COM-SPEC>\n{reference}{init}"
                        "    </NONQUEUED-SENDER-COM-SPEC>\n"
                    )
                else:
                    specs += (
                        f"    <NONQUEUED-RECEIVER-COM-SPEC>\n{reference}"
                        "      <ALIVE-TIMEOUT>0</ALIVE-TIMEOUT>\n"
                        "      <ENABLE-UPDATE>false</ENABLE-UPDATE>\n"
                        "      <HANDLE-NEVER-RECEIVED>false</HANDLE-NEVER-RECEIVED>\n"
                        "      <HANDLE-TIMEOUT-TYPE>NONE</HANDLE-TIMEOUT-TYPE>\n"
                        f"{init}    </NONQUEUED-RECEIVER-COM-SPEC>\n"
                    )
        text = f"<{tag}{self._u(port.uuid)}>\n  <SHORT-NAME>{port.name}</SHORT-NAME>\n"
        if specs:
            text += f"  <{side}-COM-SPECS>\n{specs}  </{side}-COM-SPECS>\n"
        return (
            text
            + f'  <{side}-INTERFACE-TREF DEST="{kind}">{interface.path}</{side}-INTERFACE-TREF>\n'
            + f"</{tag}>\n"
        )

    def _variable_access(self, access: AccessPlan) -> str:
        port = access.port
        assert port.interface is not None
        dest = "P-PORT-PROTOTYPE" if port.provider else "R-PORT-PROTOTYPE"
        return (
            f"    <VARIABLE-ACCESS{self._u(access.uuid)}>\n"
            f"      <SHORT-NAME>{access.name}</SHORT-NAME>\n"
            "      <ACCESSED-VARIABLE>\n"
            "        <AUTOSAR-VARIABLE-IREF>\n"
            f'          <PORT-PROTOTYPE-REF DEST="{dest}">{port.path}</PORT-PROTOTYPE-REF>\n'
            '          <TARGET-DATA-PROTOTYPE-REF DEST="VARIABLE-DATA-PROTOTYPE">'
            f"{port.interface.path}/{access.target}</TARGET-DATA-PROTOTYPE-REF>\n"
            "        </AUTOSAR-VARIABLE-IREF>\n"
            "      </ACCESSED-VARIABLE>\n"
            "    </VARIABLE-ACCESS>\n"
        )

    def _server_call(self, access: AccessPlan) -> str:
        assert access.port.interface is not None
        return (
            f"    <SYNCHRONOUS-SERVER-CALL-POINT{self._u(access.uuid)}>\n"
            f"      <SHORT-NAME>{access.name}</SHORT-NAME>\n"
            "      <OPERATION-IREF>\n"
            f'        <CONTEXT-R-PORT-REF DEST="R-PORT-PROTOTYPE">{access.port.path}'
            "</CONTEXT-R-PORT-REF>\n"
            '        <TARGET-REQUIRED-OPERATION-REF DEST="CLIENT-SERVER-OPERATION">'
            f"{access.port.interface.path}/{access.target}</TARGET-REQUIRED-OPERATION-REF>\n"
            "      </OPERATION-IREF>\n"
            "      <TIMEOUT>0</TIMEOUT>\n"
            "    </SYNCHRONOUS-SERVER-CALL-POINT>\n"
        )

    def runnable(self, runnable: RunnablePlan) -> str:
        text = (
            f"<RUNNABLE-ENTITY{self._u(runnable.uuid)}>\n"
            f"  <SHORT-NAME>{runnable.name}</SHORT-NAME>\n"
            "  <MINIMUM-START-INTERVAL>0</MINIMUM-START-INTERVAL>\n"
            "  <CAN-BE-INVOKED-CONCURRENTLY>false</CAN-BE-INVOKED-CONCURRENTLY>\n"
        )
        for kind, tag in _ACCESS_GROUPS:
            group = [access for access in runnable.accesses if access.kind is kind]
            if group:
                text += (
                    f"  <{tag}>\n" + "".join(map(self._variable_access, group)) + f"  </{tag}>\n"
                )
        calls = [a for a in runnable.accesses if a.kind is AccessKind.SYNC_SERVER_CALL]
        if calls:
            text += (
                "  <SERVER-CALL-POINTS>\n"
                + "".join(map(self._server_call, calls))
                + "  </SERVER-CALL-POINTS>\n"
            )
        return text + f"  <SYMBOL>{runnable.symbol}</SYMBOL>\n</RUNNABLE-ENTITY>\n"

    def event(self, swc: SwcPlan, event: EventPlan) -> str:
        tag = EVENT_TAGS[event.kind]
        text = (
            f"<{tag}{self._u(event.uuid)}>\n"
            f"  <SHORT-NAME>{event.name}</SHORT-NAME>\n"
            f'  <START-ON-EVENT-REF DEST="RUNNABLE-ENTITY">{swc.behavior_path}/'
            f"{event.runnable.name}</START-ON-EVENT-REF>\n"
        )
        port = event.port
        if event.kind is TriggerKind.PERIODIC:
            assert event.period_s is not None
            text += f"  <PERIOD>{number(event.period_s)}</PERIOD>\n"
        elif event.kind is TriggerKind.DATA_RECEIVED:
            assert port is not None and port.interface is not None
            text += (
                "  <DATA-IREF>\n"
                f'    <CONTEXT-R-PORT-REF DEST="R-PORT-PROTOTYPE">{port.path}'
                "</CONTEXT-R-PORT-REF>\n"
                '    <TARGET-DATA-ELEMENT-REF DEST="VARIABLE-DATA-PROTOTYPE">'
                f"{port.interface.path}/{event.target}</TARGET-DATA-ELEMENT-REF>\n"
                "  </DATA-IREF>\n"
            )
        elif event.kind is TriggerKind.OPERATION_INVOKED:
            assert port is not None and port.interface is not None
            text += (
                "  <OPERATION-IREF>\n"
                f'    <CONTEXT-P-PORT-REF DEST="P-PORT-PROTOTYPE">{port.path}'
                "</CONTEXT-P-PORT-REF>\n"
                '    <TARGET-PROVIDED-OPERATION-REF DEST="CLIENT-SERVER-OPERATION">'
                f"{port.interface.path}/{event.target}</TARGET-PROVIDED-OPERATION-REF>\n"
                "  </OPERATION-IREF>\n"
            )
        return text + f"</{tag}>\n"

    def swc(self, swc: SwcPlan) -> str:
        tag = COMPONENT_TAGS[swc.kind]
        ports = "".join(_indent(self.port(port), 2) for port in swc.ports)
        events = "".join(_indent(self.event(swc, event), 4) for event in swc.events)
        runnables = "".join(_indent(self.runnable(runnable), 4) for runnable in swc.runnables)
        return (
            f"<{tag}{self._u(swc.uuid)}>\n"
            f"  <SHORT-NAME>{swc.name}</SHORT-NAME>\n"
            f"  <PORTS>\n{ports}  </PORTS>\n"
            "  <INTERNAL-BEHAVIORS>\n"
            f"    <SWC-INTERNAL-BEHAVIOR{self._u(swc.behavior_uuid)}>\n"
            f"      <SHORT-NAME>{swc.name}_InternalBehavior</SHORT-NAME>\n"
            f"      <EVENTS>\n{events}      </EVENTS>\n"
            "      <HANDLE-TERMINATION-AND-RESTART>NO-SUPPORT</HANDLE-TERMINATION-AND-RESTART>\n"
            f"      <RUNNABLES>\n{runnables}      </RUNNABLES>\n"
            "      <SUPPORTS-MULTIPLE-INSTANTIATION>false</SUPPORTS-MULTIPLE-INSTANTIATION>\n"
            "    </SWC-INTERNAL-BEHAVIOR>\n"
            "  </INTERNAL-BEHAVIORS>\n"
            f"</{tag}>\n"
        )

    def composition(self, plan: Plan) -> str:
        prototypes = "".join(
            f"    <SW-COMPONENT-PROTOTYPE{self._u(swc.prototype_uuid)}>\n"
            f"      <SHORT-NAME>{swc.name}</SHORT-NAME>\n"
            f'      <TYPE-TREF DEST="{COMPONENT_TAGS[swc.kind]}">{swc.path}</TYPE-TREF>\n'
            "    </SW-COMPONENT-PROTOTYPE>\n"
            for swc in plan.swcs
        )
        connectors = "".join(
            f"    <ASSEMBLY-SW-CONNECTOR{self._u(connector.uuid)}>\n"
            f"      <SHORT-NAME>{connector.name}</SHORT-NAME>\n"
            "      <PROVIDER-IREF>\n"
            '        <CONTEXT-COMPONENT-REF DEST="SW-COMPONENT-PROTOTYPE">'
            f"{connector.provider.swc.prototype_path}</CONTEXT-COMPONENT-REF>\n"
            f'        <TARGET-P-PORT-REF DEST="P-PORT-PROTOTYPE">{connector.provider.path}'
            "</TARGET-P-PORT-REF>\n"
            "      </PROVIDER-IREF>\n"
            "      <REQUESTER-IREF>\n"
            '        <CONTEXT-COMPONENT-REF DEST="SW-COMPONENT-PROTOTYPE">'
            f"{connector.requester.swc.prototype_path}</CONTEXT-COMPONENT-REF>\n"
            f'        <TARGET-R-PORT-REF DEST="R-PORT-PROTOTYPE">{connector.requester.path}'
            "</TARGET-R-PORT-REF>\n"
            "      </REQUESTER-IREF>\n"
            "    </ASSEMBLY-SW-CONNECTOR>\n"
            for connector in plan.connectors
        )
        text = (
            f"<COMPOSITION-SW-COMPONENT-TYPE{self._u(plan.composition_uuid)}>\n"
            f"  <SHORT-NAME>{COMPOSITION_NAME}</SHORT-NAME>\n"
            f"  <COMPONENTS>\n{prototypes}  </COMPONENTS>\n"
        )
        if connectors:
            text += f"  <CONNECTORS>\n{connectors}  </CONNECTORS>\n"
        return text + "</COMPOSITION-SW-COMPONENT-TYPE>\n"


__all__ = ["COMPOSITION", "NAMESPACE", "SCHEMA_FILE", "Renderer", "document", "escape", "number"]
