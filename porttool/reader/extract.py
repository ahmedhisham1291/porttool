"""Extraction: lxml elements of one ARXML file -> plain-tuple records.

Only what the model needs is read. Nothing here keeps an lxml object: every value that
leaves this module is a str, int, float, bool, None or a tuple of those.

The entry points are ``dispatch_unit`` (one direct child of an AR-PACKAGE's ELEMENTS),
``reference_base`` and ``ecuc_container``. The DOM walker and the streaming walker in
``parse.py`` both call exactly these, so both strategies produce identical records.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any, Final

from porttool.core.results import Severity
from porttool.reader import codes
from porttool.reader.records import (
    RELATIVE_SEP,
    AccessRec,
    ArgumentRec,
    BaseTypeRec,
    BehaviorRec,
    ComponentRec,
    CompuMethodRec,
    ComSpecRec,
    ConnectorRec,
    ConstantRec,
    ConstraintRec,
    DataTypeRec,
    EcucContainerRec,
    ElementRec,
    EventRec,
    InterfaceRec,
    MappingSetRec,
    ModeGroupRec,
    OperationRec,
    PortRec,
    PrototypeRec,
    RawFinding,
    Ref,
    RefBaseRec,
    RunnableRec,
    UnitRec,
)

NS: Final = "http://autosar.org/schema/r4.0"
_P: Final = "{" + NS + "}"
NS_LEN: Final = len(_P)

_WARNING: Final = Severity.WARNING.value


def q(name: str) -> str:
    """Qualified lxml tag of an AUTOSAR 4.x element name."""
    return _P + name


# ---- tags used by the walkers
AUTOSAR: Final = q("AUTOSAR")
AR_PACKAGES: Final = q("AR-PACKAGES")
AR_PACKAGE: Final = q("AR-PACKAGE")
ELEMENTS: Final = q("ELEMENTS")
SHORT_NAME: Final = q("SHORT-NAME")
REFERENCE_BASES: Final = q("REFERENCE-BASES")
REFERENCE_BASE: Final = q("REFERENCE-BASE")
ECUC_MODULE: Final = q("ECUC-MODULE-CONFIGURATION-VALUES")
ECUC_CONTAINER: Final = q("ECUC-CONTAINER-VALUE")
VARIATION_POINT: Final = q("VARIATION-POINT")

# ---- tags used only here
_CATEGORY = q("CATEGORY")
_PORTS = q("PORTS")
_INTERNAL_BEHAVIORS = q("INTERNAL-BEHAVIORS")
_SWC_INTERNAL_BEHAVIOR = q("SWC-INTERNAL-BEHAVIOR")
_COMPONENTS = q("COMPONENTS")
_CONNECTORS = q("CONNECTORS")
_SW_COMPONENT_PROTOTYPE = q("SW-COMPONENT-PROTOTYPE")
_TYPE_TREF = q("TYPE-TREF")
_PROVIDED_COM_SPECS = q("PROVIDED-COM-SPECS")
_REQUIRED_COM_SPECS = q("REQUIRED-COM-SPECS")
_INIT_VALUE = q("INIT-VALUE")
_RAM_BLOCK_INIT_VALUE = q("RAM-BLOCK-INIT-VALUE")
_QUEUE_LENGTH = q("QUEUE-LENGTH")
_VALUE = q("VALUE")
_VALUE_SPEC = q("VALUE-SPEC")
_CONSTANT_REF = q("CONSTANT-REF")
_REFERENCE_VALUE_REF = q("REFERENCE-VALUE-REF")
_FIELDS = q("FIELDS")
_V = q("V")
_VT = q("VT")
_VF = q("VF")
_EVENTS = q("EVENTS")
_RUNNABLES = q("RUNNABLES")
_RUNNABLE_ENTITY = q("RUNNABLE-ENTITY")
_SYMBOL = q("SYMBOL")
_CONCURRENT = q("CAN-BE-INVOKED-CONCURRENTLY")
_MIN_START_INTERVAL = q("MINIMUM-START-INTERVAL")
_START_ON_EVENT_REF = q("START-ON-EVENT-REF")
_PERIOD = q("PERIOD")
_EVENT_SOURCE_REF = q("EVENT-SOURCE-REF")
_IDENT = q("IDENT")
_PROVIDER_IREF = q("PROVIDER-IREF")
_REQUESTER_IREF = q("REQUESTER-IREF")
_INNER_PORT_IREF = q("INNER-PORT-IREF")
_OUTER_PORT_REF = q("OUTER-PORT-REF")
_CONTEXT_COMPONENT_REF = q("CONTEXT-COMPONENT-REF")
_TARGET_P_PORT_REF = q("TARGET-P-PORT-REF")
_TARGET_R_PORT_REF = q("TARGET-R-PORT-REF")
_P_PORT_IN_COMPOSITION = q("P-PORT-IN-COMPOSITION-INSTANCE-REF")
_R_PORT_IN_COMPOSITION = q("R-PORT-IN-COMPOSITION-INSTANCE-REF")
_PROVIDED_OUTER_PORT_REF = q("PROVIDED-OUTER-PORT-REF")
_REQUIRED_OUTER_PORT_REF = q("REQUIRED-OUTER-PORT-REF")
_IS_SERVICE = q("IS-SERVICE")
_DATA_ELEMENTS = q("DATA-ELEMENTS")
_NV_DATAS = q("NV-DATAS")
_PARAMETERS = q("PARAMETERS")
_TRIGGERS = q("TRIGGERS")
_OPERATIONS = q("OPERATIONS")
_POSSIBLE_ERRORS = q("POSSIBLE-ERRORS")
_MODE_GROUP = q("MODE-GROUP")
_ARGUMENTS = q("ARGUMENTS")
_DIRECTION = q("DIRECTION")
_POSSIBLE_ERROR_REFS = q("POSSIBLE-ERROR-REFS")
_SW_DATA_DEF_PROPS = q("SW-DATA-DEF-PROPS")
_SW_DATA_DEF_PROPS_CONDITIONAL = q("SW-DATA-DEF-PROPS-CONDITIONAL")
_SW_IMPL_POLICY = q("SW-IMPL-POLICY")
_BASE_TYPE_REF = q("BASE-TYPE-REF")
_COMPU_METHOD_REF = q("COMPU-METHOD-REF")
_DATA_CONSTR_REF = q("DATA-CONSTR-REF")
_UNIT_REF = q("UNIT-REF")
_IMPL_TYPE_REF = q("IMPLEMENTATION-DATA-TYPE-REF")
_APPL_TYPE_REF = q("APPLICATION-DATA-TYPE-REF")
_SUB_ELEMENTS = q("SUB-ELEMENTS")
_ARRAY_SIZE = q("ARRAY-SIZE")
_ELEMENT = q("ELEMENT")
_MAX_NUMBER_OF_ELEMENTS = q("MAX-NUMBER-OF-ELEMENTS")
_BASE_TYPE_SIZE = q("BASE-TYPE-SIZE")
_BASE_TYPE_ENCODING = q("BASE-TYPE-ENCODING")
_COMPU_INTERNAL_TO_PHYS = q("COMPU-INTERNAL-TO-PHYS")
_COMPU_SCALE = q("COMPU-SCALE")
_LOWER_LIMIT = q("LOWER-LIMIT")
_UPPER_LIMIT = q("UPPER-LIMIT")
_COMPU_NUMERATOR = q("COMPU-NUMERATOR")
_COMPU_DENOMINATOR = q("COMPU-DENOMINATOR")
_COMPU_CONST = q("COMPU-CONST")
_INTERNAL_CONSTRS = q("INTERNAL-CONSTRS")
_PHYS_CONSTRS = q("PHYS-CONSTRS")
_DISPLAY_NAME = q("DISPLAY-NAME")
_DATA_TYPE_MAP = q("DATA-TYPE-MAP")
_MODE_DECLARATION = q("MODE-DECLARATION")
_SHORT_LABEL = q("SHORT-LABEL")
_IS_DEFAULT = q("IS-DEFAULT")
_IS_GLOBAL = q("IS-GLOBAL")
_BASE_IS_THIS_PACKAGE = q("BASE-IS-THIS-PACKAGE")
_PACKAGE_REF = q("PACKAGE-REF")
_DEFINITION_REF = q("DEFINITION-REF")
_PARAMETER_VALUES = q("PARAMETER-VALUES")
_REFERENCE_VALUES = q("REFERENCE-VALUES")
_VALUE_REF = q("VALUE-REF")

_COMPONENT_SUFFIX = "-SW-COMPONENT-TYPE"

_PORT_TAGS: Final = {
    q("P-PORT-PROTOTYPE"): "P-PORT-PROTOTYPE",
    q("R-PORT-PROTOTYPE"): "R-PORT-PROTOTYPE",
    q("PR-PORT-PROTOTYPE"): "PR-PORT-PROTOTYPE",
}
_INTERFACE_TREFS: Final = frozenset(
    {
        q("PROVIDED-INTERFACE-TREF"),
        q("REQUIRED-INTERFACE-TREF"),
        q("PROVIDED-REQUIRED-INTERFACE-TREF"),
    }
)
_COM_SPEC_REFS: Final = frozenset(
    {
        q("DATA-ELEMENT-REF"),
        q("OPERATION-REF"),
        q("MODE-GROUP-REF"),
        q("PARAMETER-REF"),
        q("VARIABLE-REF"),
    }
)

#: Runnable child lists that hold port accesses.
_ACCESS_LISTS: Final = frozenset(
    {
        q("DATA-READ-ACCESSS"),
        q("DATA-WRITE-ACCESSS"),
        q("DATA-SEND-POINTS"),
        q("DATA-RECEIVE-POINT-BY-ARGUMENTS"),
        q("DATA-RECEIVE-POINT-BY-VALUES"),
        q("SERVER-CALL-POINTS"),
        q("MODE-ACCESS-POINTS"),
        q("MODE-SWITCH-POINTS"),
        q("PARAMETER-ACCESSS"),
        q("EXTERNAL-TRIGGERING-POINTS"),
    }
)

#: References to the port inside an instance reference.
_PORT_REFS: Final = frozenset(
    {
        q("PORT-PROTOTYPE-REF"),
        q("CONTEXT-R-PORT-REF"),
        q("CONTEXT-P-PORT-REF"),
        q("CONTEXT-PORT-REF"),
    }
)
#: References to the accessed element inside an instance reference.
_TARGET_REFS: Final = frozenset(
    {
        q("TARGET-DATA-PROTOTYPE-REF"),
        q("TARGET-DATA-ELEMENT-REF"),
        q("TARGET-REQUIRED-OPERATION-REF"),
        q("TARGET-PROVIDED-OPERATION-REF"),
        q("TARGET-MODE-GROUP-REF"),
        q("TARGET-MODE-DECLARATION-REF"),
        q("TARGET-TRIGGER-REF"),
    }
)
_IREF_PARTS: Final = tuple(sorted(_PORT_REFS | _TARGET_REFS))

#: Events that never carry an instance reference: skip the subtree scan for them.
_PLAIN_EVENTS: Final = frozenset({q("TIMING-EVENT"), q("INIT-EVENT"), q("BACKGROUND-EVENT")})

_PROPS_REFS: Final = frozenset(
    {_BASE_TYPE_REF, _COMPU_METHOD_REF, _DATA_CONSTR_REF, _UNIT_REF, _IMPL_TYPE_REF}
)

_TRUE: Final = frozenset({"true", "1"})

_intern = sys.intern


class Collector:
    """Mutable lists that one file's extraction appends to."""

    __slots__ = (
        "pkg",
        "check_variation",
        "ecuc_suffixes",
        "findings",
        "ref_bases",
        "components",
        "ports",
        "behaviors",
        "runnables",
        "accesses",
        "events",
        "prototypes",
        "connectors",
        "interfaces",
        "data_types",
        "base_types",
        "compu_methods",
        "constraints",
        "units",
        "mapping_sets",
        "constants",
        "mode_groups",
        "ecuc_containers",
    )

    def __init__(self, ecuc_suffixes: tuple[str, ...], *, check_variation: bool = True) -> None:
        #: Path of the package being walked; relative references are recorded against it.
        self.pkg: str = ""
        self.check_variation = check_variation
        #: DEFINITION-REF suffixes of the ECUC containers to keep.
        self.ecuc_suffixes = ecuc_suffixes
        self.findings: list[RawFinding] = []
        self.ref_bases: list[RefBaseRec] = []
        self.components: list[ComponentRec] = []
        self.ports: list[PortRec] = []
        self.behaviors: list[BehaviorRec] = []
        self.runnables: list[RunnableRec] = []
        self.accesses: list[AccessRec] = []
        self.events: list[EventRec] = []
        self.prototypes: list[PrototypeRec] = []
        self.connectors: list[ConnectorRec] = []
        self.interfaces: list[InterfaceRec] = []
        self.data_types: list[DataTypeRec] = []
        self.base_types: list[BaseTypeRec] = []
        self.compu_methods: list[CompuMethodRec] = []
        self.constraints: list[ConstraintRec] = []
        self.units: list[UnitRec] = []
        self.mapping_sets: list[MappingSetRec] = []
        self.constants: list[ConstantRec] = []
        self.mode_groups: list[ModeGroupRec] = []
        self.ecuc_containers: list[EcucContainerRec] = []

    def missing_name(self, el: Any, owner_path: str) -> None:
        local = _local(el)
        self.findings.append(
            (
                codes.NAME_MISSING,
                _WARNING,
                f"A {local} without SHORT-NAME was skipped.",
                owner_path,
                _line(el),
            )
        )


# --------------------------------------------------------------------------- helpers


def _local(el: Any) -> str:
    """Element name without namespace. ``?`` for a node that is not an element."""
    tag = el.tag
    if not isinstance(tag, str):
        return "?"
    return _intern(tag[NS_LEN:]) if tag.startswith(_P) else tag


def _line(el: Any) -> int | None:
    line = el.sourceline
    return line if isinstance(line, int) else None


def _clean(text: str | None) -> str | None:
    if text is None:
        return None
    text = text.strip()
    return text or None


def _child(el: Any, tag: str) -> Any:
    """First direct child with ``tag`` or None (C-level search)."""
    return next(el.iterchildren(tag), None)


def _child_text(el: Any, tag: str) -> str | None:
    child = next(el.iterchildren(tag), None)
    return None if child is None else _clean(child.text)


def short_name(el: Any) -> str | None:
    return _child_text(el, SHORT_NAME)


def _ref(el: Any, out: Collector) -> Ref | None:
    text = el.text
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None
    if text[0] != "/":
        text = f"{el.get('BASE') or ''}{RELATIVE_SEP}{out.pkg}{RELATIVE_SEP}{text}"
    dest = el.get("DEST")
    return (text, None if dest is None else _intern(dest))


def _child_ref(el: Any, tag: str, out: Collector) -> Ref | None:
    child = next(el.iterchildren(tag), None)
    return None if child is None else _ref(child, out)


def _last(path: str) -> str:
    return path.rpartition("/")[2]


def parse_int(text: str | None) -> int | None:
    """Integer from ARXML text: decimal, 0x, 0b, 0o, or a float without fraction."""
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None
    try:
        return int(text, 0)
    except ValueError:
        pass
    try:
        value = float(text)
    except ValueError:
        return None
    return int(value) if value.is_integer() else None


def parse_float(text: str | None) -> float | None:
    """Float from ARXML text: decimal, exponent, INF / -INF, or an integer literal."""
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    try:
        return float(int(text, 0))
    except ValueError:
        return None


def _bool(text: str | None) -> bool:
    return text is not None and text.strip().lower() in _TRUE


def value_text(spec: Any) -> str | None:
    """Textual form of one value specification element.

    Numerical and text values give their VALUE text, a constant reference gives the path
    of the constant, arrays give ``[a, b]``, records give ``{a, b}``. Kinds the reader
    does not interpret give ``<ELEMENT-NAME>`` so that nothing looks like an empty value.
    """
    local = _local(spec)
    if local in ("NUMERICAL-VALUE-SPECIFICATION", "TEXT-VALUE-SPECIFICATION"):
        return _child_text(spec, _VALUE)
    if local == "CONSTANT-REFERENCE":
        return _child_text(spec, _CONSTANT_REF)
    if local == "ARRAY-VALUE-SPECIFICATION":
        holder = _child(spec, ELEMENTS)
        items = [] if holder is None else [value_text(item) or "?" for item in holder]
        return "[" + ", ".join(items) + "]"
    if local == "RECORD-VALUE-SPECIFICATION":
        holder = _child(spec, _FIELDS)
        items = [] if holder is None else [value_text(item) or "?" for item in holder]
        return "{" + ", ".join(items) + "}"
    if local == "APPLICATION-VALUE-SPECIFICATION":
        values = [text for text in (_clean(v.text) for v in spec.iter(_V, _VT, _VF)) if text]
        if len(values) == 1:
            return values[0]
        return "[" + ", ".join(values) + "]" if values else None
    if local == "REFERENCE-VALUE-SPECIFICATION":
        target = _child_text(spec, _REFERENCE_VALUE_REF)
        return None if target is None else "&" + target
    return f"<{local}>"


def _holder_value(holder: Any) -> str | None:
    """Value text of INIT-VALUE / VALUE-SPEC: their single child is the specification."""
    for spec in holder:
        return value_text(spec)
    return None


# --------------------------------------------------------------------------- components


def _component(unit: Any, out: Collector) -> None:
    name = None
    ports = behaviors = prototypes = connectors = None
    for child in unit:
        tag = child.tag
        if tag == SHORT_NAME:
            name = _clean(child.text)
        elif tag == _PORTS:
            ports = child
        elif tag == _INTERNAL_BEHAVIORS:
            behaviors = child
        elif tag == _COMPONENTS:
            prototypes = child
        elif tag == _CONNECTORS:
            connectors = child
    if name is None:
        out.missing_name(unit, out.pkg)
        return
    path = f"{out.pkg}/{name}"
    out.components.append((path, name, _local(unit), _line(unit), unit.get("UUID")))
    if ports is not None:
        _ports_of(ports, path, out)
    if behaviors is not None:
        for behavior in behaviors.iterchildren(_SWC_INTERNAL_BEHAVIOR):
            _behavior(behavior, path, out)
    if prototypes is not None:
        _prototypes_of(prototypes, path, out)
    if connectors is not None:
        _connectors_of(connectors, path, out)


def _ports_of(ports: Any, swc_path: str, out: Collector) -> None:
    add = out.ports.append
    port_tags = _PORT_TAGS
    for el in ports:
        local = port_tags.get(el.tag)
        if local is None:
            continue
        name = None
        interface: Ref | None = None
        specs: tuple[ComSpecRec, ...] = ()
        for child in el:
            tag = child.tag
            if tag == SHORT_NAME:
                name = _clean(child.text)
            elif tag in _INTERFACE_TREFS:
                interface = _ref(child, out)
            elif tag == _PROVIDED_COM_SPECS or tag == _REQUIRED_COM_SPECS:
                specs += _com_specs(child, out)
        if name is None:
            out.missing_name(el, swc_path)
            continue
        add(
            (
                f"{swc_path}/{name}",
                name,
                swc_path,
                local,
                interface,
                specs,
                _line(el),
                el.get("UUID"),
            )
        )


def _com_specs(holder: Any, out: Collector) -> tuple[ComSpecRec, ...]:
    result: list[ComSpecRec] = []
    for spec in holder:
        target: Ref | None = None
        init: str | None = None
        ram_init: str | None = None
        queue: int | None = None
        for child in spec:
            tag = child.tag
            if tag in _COM_SPEC_REFS:
                target = _ref(child, out)
            elif tag == _INIT_VALUE:
                init = _holder_value(child)
            elif tag == _RAM_BLOCK_INIT_VALUE:
                ram_init = _holder_value(child)
            elif tag == _QUEUE_LENGTH:
                queue = parse_int(child.text)
        result.append((_local(spec), target, init if init is not None else ram_init, queue))
    return tuple(result)


def _prototypes_of(holder: Any, composition_path: str, out: Collector) -> None:
    for el in holder.iterchildren(_SW_COMPONENT_PROTOTYPE):
        name = None
        type_ref: Ref | None = None
        for child in el:
            tag = child.tag
            if tag == SHORT_NAME:
                name = _clean(child.text)
            elif tag == _TYPE_TREF:
                type_ref = _ref(child, out)
        if name is None:
            out.missing_name(el, composition_path)
            continue
        out.prototypes.append(
            (
                f"{composition_path}/{name}",
                name,
                composition_path,
                type_ref,
                _line(el),
                el.get("UUID"),
            )
        )


def _instance_end(iref: Any, out: Collector) -> tuple[Ref | None, Ref | None]:
    """(context component ref, target port ref) of one end of a connector."""
    component: Ref | None = None
    port: Ref | None = None
    for child in iref:
        tag = child.tag
        if tag == _CONTEXT_COMPONENT_REF:
            component = _ref(child, out)
        elif tag == _TARGET_P_PORT_REF or tag == _TARGET_R_PORT_REF:
            port = _ref(child, out)
    return component, port


def _connectors_of(holder: Any, composition_path: str, out: Collector) -> None:
    add = out.connectors.append
    for el in holder:
        name = None
        p_comp: Ref | None = None
        p_port: Ref | None = None
        r_comp: Ref | None = None
        r_port: Ref | None = None
        outer: Ref | None = None
        inner_is_provider: bool | None = None
        for child in el:
            tag = child.tag
            if tag == SHORT_NAME:
                name = _clean(child.text)
            elif tag == _PROVIDER_IREF:
                p_comp, p_port = _instance_end(child, out)
            elif tag == _REQUESTER_IREF:
                r_comp, r_port = _instance_end(child, out)
            elif tag == _INNER_PORT_IREF:
                for inner in child:
                    if inner.tag == _P_PORT_IN_COMPOSITION:
                        inner_is_provider = True
                        p_comp, p_port = _instance_end(inner, out)
                    elif inner.tag == _R_PORT_IN_COMPOSITION:
                        inner_is_provider = False
                        r_comp, r_port = _instance_end(inner, out)
            elif tag == _OUTER_PORT_REF:
                outer = _ref(child, out)
            elif tag == _PROVIDED_OUTER_PORT_REF:
                p_port = _ref(child, out)
            elif tag == _REQUIRED_OUTER_PORT_REF:
                r_port = _ref(child, out)
        if name is None:
            out.missing_name(el, composition_path)
            continue
        if inner_is_provider is True:
            # inner P port -> the composition's outer port
            r_comp, r_port = None, outer
        elif inner_is_provider is False:
            # the composition's outer port -> inner R port
            p_comp, p_port = None, outer
        elif outer is not None:
            # delegation without inner port: keep the outer port on the provider side
            p_port = outer
        add(
            (
                f"{composition_path}/{name}",
                name,
                composition_path,
                _local(el),
                p_comp,
                p_port,
                r_comp,
                r_port,
                _line(el),
                el.get("UUID"),
            )
        )


# --------------------------------------------------------------------------- behavior


def _behavior(el: Any, swc_path: str, out: Collector) -> None:
    name = None
    events = runnables = None
    for child in el:
        tag = child.tag
        if tag == SHORT_NAME:
            name = _clean(child.text)
        elif tag == _EVENTS:
            events = child
        elif tag == _RUNNABLES:
            runnables = child
    if name is None:
        out.missing_name(el, swc_path)
        return
    path = f"{swc_path}/{name}"
    out.behaviors.append((path, name, swc_path, _line(el), el.get("UUID")))
    if runnables is not None:
        for runnable in runnables.iterchildren(_RUNNABLE_ENTITY):
            _runnable(runnable, path, swc_path, out)
    if events is not None:
        _events_of(events, path, swc_path, out)


def _runnable(el: Any, behavior_path: str, swc_path: str, out: Collector) -> None:
    name = None
    symbol = None
    concurrent = False
    interval: float | None = None
    access_lists: list[Any] = []
    for child in el:
        tag = child.tag
        if tag == SHORT_NAME:
            name = _clean(child.text)
        elif tag == _SYMBOL:
            symbol = _clean(child.text)
        elif tag == _CONCURRENT:
            concurrent = _bool(child.text)
        elif tag == _MIN_START_INTERVAL:
            interval = parse_float(child.text)
        elif tag in _ACCESS_LISTS:
            access_lists.append(child)
    if name is None:
        out.missing_name(el, behavior_path)
        return
    path = f"{behavior_path}/{name}"
    out.runnables.append(
        (
            path,
            name,
            behavior_path,
            swc_path,
            symbol,
            concurrent,
            interval,
            _line(el),
            el.get("UUID"),
        )
    )
    for holder in access_lists:
        _accesses_of(holder, path, swc_path, out)


def _accesses_of(holder: Any, runnable_path: str, swc_path: str, out: Collector) -> None:
    add = out.accesses.append
    list_local = _local(holder)
    for el in holder:
        port: Ref | None = None
        target: Ref | None = None
        for part in el.iter(*_IREF_PARTS):
            if part.tag in _PORT_REFS:
                if port is None:
                    port = _ref(part, out)
            elif target is None:
                target = _ref(part, out)
        if port is None and target is None:
            # access to a local variable or parameter: not a port access
            continue
        name = short_name(el)
        if name is None:
            ident = _child(el, _IDENT)
            if ident is not None:
                name = short_name(ident)
        local = _local(el)
        if name is None:
            # MODE-ACCESS-POINT has no SHORT-NAME in older 4.x schemas: build a stable name
            port_name = _last(port[0]) if port is not None else "?"
            target_name = _last(target[0]) if target is not None else "?"
            name = f"<{local}:{port_name}.{target_name}>"
        kind = local if local != "VARIABLE-ACCESS" else list_local
        add(
            (
                f"{runnable_path}/{name}",
                name,
                runnable_path,
                swc_path,
                kind,
                port,
                target,
                _line(el),
                el.get("UUID"),
            )
        )


def _events_of(holder: Any, behavior_path: str, swc_path: str, out: Collector) -> None:
    add = out.events.append
    for el in holder:
        name = None
        runnable: Ref | None = None
        period: float | None = None
        source: Ref | None = None
        for child in el:
            tag = child.tag
            if tag == SHORT_NAME:
                name = _clean(child.text)
            elif tag == _START_ON_EVENT_REF:
                runnable = _ref(child, out)
            elif tag == _PERIOD:
                period = parse_float(child.text)
            elif tag == _EVENT_SOURCE_REF:
                source = _ref(child, out)
        if name is None:
            out.missing_name(el, behavior_path)
            continue
        port: Ref | None = None
        target: Ref | None = None
        target2: Ref | None = None
        if el.tag not in _PLAIN_EVENTS:
            for part in el.iter(*_IREF_PARTS):
                if part.tag in _PORT_REFS:
                    if port is None:
                        port = _ref(part, out)
                elif target is None:
                    target = _ref(part, out)
                elif target2 is None:
                    target2 = _ref(part, out)
        add(
            (
                f"{behavior_path}/{name}",
                name,
                behavior_path,
                swc_path,
                _local(el),
                runnable,
                port,
                target,
                target2,
                period,
                source,
                _line(el),
                el.get("UUID"),
            )
        )


# --------------------------------------------------------------------------- interfaces


def _is_queued(el: Any) -> bool:
    props = _child(el, _SW_DATA_DEF_PROPS)
    if props is None:
        return False
    for policy in props.iter(_SW_IMPL_POLICY):
        return _clean(policy.text) == "QUEUED"
    return False


def _data_prototypes(holder: Any, out: Collector) -> list[ElementRec]:
    result: list[ElementRec] = []
    for el in holder:
        name = None
        type_ref: Ref | None = None
        init: str | None = None
        for child in el:
            tag = child.tag
            if tag == SHORT_NAME:
                name = _clean(child.text)
            elif tag == _TYPE_TREF:
                type_ref = _ref(child, out)
            elif tag == _INIT_VALUE:
                init = _holder_value(child)
        if name is None:
            out.missing_name(el, out.pkg)
            continue
        result.append((name, _local(el), type_ref, _is_queued(el), init))
    return result


def _operation(el: Any, out: Collector) -> OperationRec | None:
    name = None
    arguments: list[ArgumentRec] = []
    errors: list[Ref] = []
    for child in el:
        tag = child.tag
        if tag == SHORT_NAME:
            name = _clean(child.text)
        elif tag == _ARGUMENTS:
            for arg in child:
                arg_name = None
                direction = None
                type_ref: Ref | None = None
                for part in arg:
                    ptag = part.tag
                    if ptag == SHORT_NAME:
                        arg_name = _clean(part.text)
                    elif ptag == _DIRECTION:
                        direction = _clean(part.text)
                    elif ptag == _TYPE_TREF:
                        type_ref = _ref(part, out)
                if arg_name is None:
                    out.missing_name(arg, out.pkg)
                    continue
                arguments.append((arg_name, direction, type_ref))
        elif tag == _POSSIBLE_ERROR_REFS:
            for error in child:
                ref = _ref(error, out)
                if ref is not None:
                    errors.append(ref)
    if name is None:
        out.missing_name(el, out.pkg)
        return None
    return (name, tuple(arguments), tuple(errors))


def _interface(unit: Any, out: Collector) -> None:
    name = None
    is_service = False
    elements: list[ElementRec] = []
    operations: list[OperationRec] = []
    errors: list[str] = []
    for child in unit:
        tag = child.tag
        if tag == SHORT_NAME:
            name = _clean(child.text)
        elif tag == _IS_SERVICE:
            is_service = _bool(child.text)
        elif tag == _DATA_ELEMENTS or tag == _NV_DATAS or tag == _PARAMETERS:
            elements.extend(_data_prototypes(child, out))
        elif tag == _TRIGGERS:
            for trigger in child:
                trigger_name = short_name(trigger)
                if trigger_name is None:
                    out.missing_name(trigger, out.pkg)
                else:
                    elements.append((trigger_name, _local(trigger), None, False, None))
        elif tag == _MODE_GROUP:
            group_name = short_name(child)
            if group_name is None:
                out.missing_name(child, out.pkg)
            else:
                elements.append(
                    (
                        group_name,
                        "MODE-DECLARATION-GROUP-PROTOTYPE",
                        _child_ref(child, _TYPE_TREF, out),
                        False,
                        None,
                    )
                )
        elif tag == _OPERATIONS:
            for op in child:
                operation = _operation(op, out)
                if operation is not None:
                    operations.append(operation)
        elif tag == _POSSIBLE_ERRORS:
            for error in child:
                error_name = short_name(error)
                if error_name is not None:
                    errors.append(error_name)
    if name is None:
        out.missing_name(unit, out.pkg)
        return
    out.interfaces.append(
        (
            f"{out.pkg}/{name}",
            name,
            _local(unit),
            is_service,
            tuple(elements),
            tuple(operations),
            tuple(errors),
            _line(unit),
            unit.get("UUID"),
        )
    )


# --------------------------------------------------------------------------- data types


def _props_refs(el: Any, out: Collector) -> dict[str, Ref | None]:
    """References inside the direct SW-DATA-DEF-PROPS child of ``el``, by tag."""
    found: dict[str, Ref | None] = {}
    props = _child(el, _SW_DATA_DEF_PROPS)
    if props is not None:
        # Only the first (outermost) conditional: nested ones describe pointer targets.
        for conditional in props.iter(_SW_DATA_DEF_PROPS_CONDITIONAL):
            for ref in conditional:
                if ref.tag in _PROPS_REFS:
                    found.setdefault(ref.tag, _ref(ref, out))
            break
    return found


def _implementation_type(unit: Any, out: Collector) -> None:
    name = short_name(unit)
    if name is None:
        out.missing_name(unit, out.pkg)
        return
    refs = _props_refs(unit, out)
    element_names: list[str] = []
    element_refs: list[Ref | None] = []
    array_size: int | None = None
    subs = _child(unit, _SUB_ELEMENTS)
    if subs is not None:
        for sub in subs:
            sub_name = short_name(sub)
            if sub_name is None:
                out.missing_name(sub, f"{out.pkg}/{name}")
                continue
            sub_refs = _props_refs(sub, out)
            element_names.append(sub_name)
            element_refs.append(sub_refs.get(_IMPL_TYPE_REF) or sub_refs.get(_BASE_TYPE_REF))
            if array_size is None:
                array_size = parse_int(_child_text(sub, _ARRAY_SIZE))
    out.data_types.append(
        (
            f"{out.pkg}/{name}",
            name,
            _local(unit),
            _child_text(unit, _CATEGORY),
            refs.get(_BASE_TYPE_REF),
            refs.get(_COMPU_METHOD_REF),
            refs.get(_DATA_CONSTR_REF),
            refs.get(_UNIT_REF),
            refs.get(_IMPL_TYPE_REF),
            tuple(element_names),
            tuple(element_refs),
            array_size,
            _line(unit),
            unit.get("UUID"),
        )
    )


def _application_type(unit: Any, out: Collector) -> None:
    name = short_name(unit)
    if name is None:
        out.missing_name(unit, out.pkg)
        return
    refs = _props_refs(unit, out)
    element_names: list[str] = []
    element_refs: list[Ref | None] = []
    array_size: int | None = None
    local = _local(unit)
    if local == "APPLICATION-ARRAY-DATA-TYPE":
        element = _child(unit, _ELEMENT)
        if element is not None:
            element_names.append(short_name(element) or "")
            element_refs.append(_child_ref(element, _TYPE_TREF, out))
            array_size = parse_int(_child_text(element, _MAX_NUMBER_OF_ELEMENTS))
    elif local == "APPLICATION-RECORD-DATA-TYPE":
        holder = _child(unit, ELEMENTS)
        if holder is not None:
            for element in holder:
                element_name = short_name(element)
                if element_name is None:
                    out.missing_name(element, f"{out.pkg}/{name}")
                    continue
                element_names.append(element_name)
                element_refs.append(_child_ref(element, _TYPE_TREF, out))
    out.data_types.append(
        (
            f"{out.pkg}/{name}",
            name,
            local,
            _child_text(unit, _CATEGORY),
            None,
            refs.get(_COMPU_METHOD_REF),
            refs.get(_DATA_CONSTR_REF),
            refs.get(_UNIT_REF),
            None,
            tuple(element_names),
            tuple(element_refs),
            array_size,
            _line(unit),
            unit.get("UUID"),
        )
    )


def _base_type(unit: Any, out: Collector) -> None:
    name = short_name(unit)
    if name is None:
        out.missing_name(unit, out.pkg)
        return
    out.base_types.append(
        (
            f"{out.pkg}/{name}",
            name,
            parse_int(_child_text(unit, _BASE_TYPE_SIZE)),
            _child_text(unit, _BASE_TYPE_ENCODING),
            _line(unit),
            unit.get("UUID"),
        )
    )


def _coefficients(holder: Any) -> list[float | None]:
    return [parse_float(v.text) for v in holder.iterchildren(_V)]


def _compu_method(unit: Any, out: Collector) -> None:
    name = short_name(unit)
    if name is None:
        out.missing_name(unit, out.pkg)
        return
    factor: float | None = None
    offset: float | None = None
    enum_values: list[tuple[int, str]] = []
    conversion = _child(unit, _COMPU_INTERNAL_TO_PHYS)
    if conversion is not None:
        for scale in conversion.iter(_COMPU_SCALE):
            text = None
            for const in scale.iterchildren(_COMPU_CONST):
                text = _child_text(const, _VT)
            if text is not None:
                number = parse_int(_child_text(scale, _LOWER_LIMIT))
                if number is not None:
                    enum_values.append((number, text))
                continue
            if factor is not None:
                continue  # only the first rational scale describes a linear method
            numerator: list[float | None] = []
            denominator: list[float | None] = []
            for part in scale.iter(_COMPU_NUMERATOR, _COMPU_DENOMINATOR):
                if part.tag == _COMPU_NUMERATOR:
                    numerator = _coefficients(part)
                else:
                    denominator = _coefficients(part)
            if len(numerator) >= 2 and numerator[0] is not None and numerator[1] is not None:
                divisor = denominator[0] if denominator and denominator[0] else 1.0
                offset = numerator[0] / divisor
                factor = numerator[1] / divisor
    out.compu_methods.append(
        (
            f"{out.pkg}/{name}",
            name,
            _child_text(unit, _CATEGORY),
            factor,
            offset,
            tuple(enum_values),
            _child_ref(unit, _UNIT_REF, out),
            _line(unit),
            unit.get("UUID"),
        )
    )


def _constraint(unit: Any, out: Collector) -> None:
    name = short_name(unit)
    if name is None:
        out.missing_name(unit, out.pkg)
        return
    lower: float | None = None
    upper: float | None = None
    limits = next(unit.iter(_INTERNAL_CONSTRS), None)
    if limits is None:
        limits = next(unit.iter(_PHYS_CONSTRS), None)
    if limits is not None:
        lower = parse_float(_child_text(limits, _LOWER_LIMIT))
        upper = parse_float(_child_text(limits, _UPPER_LIMIT))
    out.constraints.append((f"{out.pkg}/{name}", name, lower, upper, _line(unit), unit.get("UUID")))


def _unit(unit: Any, out: Collector) -> None:
    name = short_name(unit)
    if name is None:
        out.missing_name(unit, out.pkg)
        return
    out.units.append(
        (
            f"{out.pkg}/{name}",
            name,
            _child_text(unit, _DISPLAY_NAME),
            _line(unit),
            unit.get("UUID"),
        )
    )


def _mapping_set(unit: Any, out: Collector) -> None:
    name = short_name(unit)
    if name is None:
        out.missing_name(unit, out.pkg)
        return
    pairs: list[tuple[Ref | None, Ref | None]] = []
    for entry in unit.iter(_DATA_TYPE_MAP):
        pairs.append(
            (_child_ref(entry, _APPL_TYPE_REF, out), _child_ref(entry, _IMPL_TYPE_REF, out))
        )
    out.mapping_sets.append(
        (f"{out.pkg}/{name}", name, tuple(pairs), _line(unit), unit.get("UUID"))
    )


def _constant(unit: Any, out: Collector) -> None:
    name = short_name(unit)
    if name is None:
        out.missing_name(unit, out.pkg)
        return
    holder = _child(unit, _VALUE_SPEC)
    value = None if holder is None else _holder_value(holder)
    out.constants.append((f"{out.pkg}/{name}", name, value, _line(unit), unit.get("UUID")))


def _mode_group(unit: Any, out: Collector) -> None:
    name = short_name(unit)
    if name is None:
        out.missing_name(unit, out.pkg)
        return
    modes = [mode for mode in (short_name(el) for el in unit.iter(_MODE_DECLARATION)) if mode]
    out.mode_groups.append((f"{out.pkg}/{name}", name, tuple(modes), _line(unit), unit.get("UUID")))


# --------------------------------------------------------------------------- ECUC


def identifiable_path(el: Any) -> str | None:
    """Absolute short-name path of ``el``, read from its ancestors. None without a name."""
    name = short_name(el)
    if name is None:
        return None
    names = [name]
    for ancestor in el.iterancestors():
        ancestor_name = short_name(ancestor)
        if ancestor_name is not None:
            names.append(ancestor_name)
    names.reverse()
    return "/" + "/".join(names)


def ecuc_container(el: Any, out: Collector) -> None:
    """Record one ECUC-CONTAINER-VALUE if its definition is one the model needs."""
    definition = _child_text(el, _DEFINITION_REF)
    if definition is None or not definition.endswith(out.ecuc_suffixes):
        return
    path = identifiable_path(el)
    if path is None:
        out.missing_name(el, out.pkg)
        return
    params: list[tuple[str, str | None]] = []
    refs: list[tuple[str, str | None, str | None]] = []
    for child in el:
        tag = child.tag
        if tag == _PARAMETER_VALUES:
            for value in child:
                param_definition = _child_text(value, _DEFINITION_REF)
                if param_definition is not None:
                    params.append((param_definition, _child_text(value, _VALUE)))
        elif tag == _REFERENCE_VALUES:
            for value in child:
                ref_definition = _child_text(value, _DEFINITION_REF)
                target = _child(value, _VALUE_REF)
                if ref_definition is not None and target is not None:
                    refs.append((ref_definition, _clean(target.text), target.get("DEST")))
    out.ecuc_containers.append(
        (path, _last(path), definition, tuple(params), tuple(refs), _line(el), el.get("UUID"))
    )


def _ecuc_module(unit: Any, out: Collector) -> None:
    for container in unit.iter(ECUC_CONTAINER):
        ecuc_container(container, out)


# --------------------------------------------------------------------------- packages


def reference_base(el: Any, pkg: str, out: Collector) -> None:
    """Record one REFERENCE-BASE of the package ``pkg``."""
    label = _child_text(el, _SHORT_LABEL)
    if label is None:
        return
    target: str | None
    if _bool(_child_text(el, _BASE_IS_THIS_PACKAGE)):
        target = pkg
    else:
        target = _child_text(el, _PACKAGE_REF)
    out.ref_bases.append(
        (
            pkg,
            label,
            _bool(_child_text(el, _IS_DEFAULT)),
            _bool(_child_text(el, _IS_GLOBAL)),
            target,
            _line(el),
        )
    )


def _variation_points(unit: Any, out: Collector) -> None:
    for point in unit.iter(VARIATION_POINT):
        names: list[str] = []
        node = point.getparent()
        while node is not None:
            name = short_name(node)
            if name is not None:
                names.append(name)
            if node is unit:
                break
            node = node.getparent()
        names.reverse()
        path = "/".join([out.pkg, *names])
        out.findings.append(
            (
                codes.VARIATION_POINT,
                _WARNING,
                "Variation points are not supported: the object is treated as always present.",
                path,
                _line(point),
            )
        )


_Handler = Callable[[Any, Collector], None]

_HANDLERS: Final[dict[str, _Handler]] = {
    q("APPLICATION-SW-COMPONENT-TYPE"): _component,
    q("SENSOR-ACTUATOR-SW-COMPONENT-TYPE"): _component,
    q("COMPLEX-DEVICE-DRIVER-SW-COMPONENT-TYPE"): _component,
    q("ECU-ABSTRACTION-SW-COMPONENT-TYPE"): _component,
    q("SERVICE-SW-COMPONENT-TYPE"): _component,
    q("SERVICE-PROXY-SW-COMPONENT-TYPE"): _component,
    q("NV-BLOCK-SW-COMPONENT-TYPE"): _component,
    q("PARAMETER-SW-COMPONENT-TYPE"): _component,
    q("COMPOSITION-SW-COMPONENT-TYPE"): _component,
    q("SENDER-RECEIVER-INTERFACE"): _interface,
    q("CLIENT-SERVER-INTERFACE"): _interface,
    q("MODE-SWITCH-INTERFACE"): _interface,
    q("PARAMETER-INTERFACE"): _interface,
    q("NV-DATA-INTERFACE"): _interface,
    q("TRIGGER-INTERFACE"): _interface,
    q("IMPLEMENTATION-DATA-TYPE"): _implementation_type,
    q("APPLICATION-PRIMITIVE-DATA-TYPE"): _application_type,
    q("APPLICATION-ARRAY-DATA-TYPE"): _application_type,
    q("APPLICATION-RECORD-DATA-TYPE"): _application_type,
    q("SW-BASE-TYPE"): _base_type,
    q("COMPU-METHOD"): _compu_method,
    q("DATA-CONSTR"): _constraint,
    q("UNIT"): _unit,
    q("DATA-TYPE-MAPPING-SET"): _mapping_set,
    q("CONSTANT-SPECIFICATION"): _constant,
    q("MODE-DECLARATION-GROUP"): _mode_group,
}


def dispatch_unit(unit: Any, pkg: str, out: Collector, *, ecuc_done: bool = False) -> None:
    """Extract one direct child of an AR-PACKAGE's ELEMENTS.

    ``ecuc_done`` is True in the streaming walker, which has already seen every ECUC
    container of the unit one by one.
    """
    tag = unit.tag
    handler = _HANDLERS.get(tag)
    if handler is None:
        if tag == ECUC_MODULE:
            if not ecuc_done:
                out.pkg = pkg
                _ecuc_module(unit, out)
            return
        if not (isinstance(tag, str) and tag.endswith(_COMPONENT_SUFFIX) and tag.startswith(_P)):
            return
        handler = _component  # unknown component kind: the builder reports it
    out.pkg = pkg
    handler(unit, out)
    if out.check_variation:
        _variation_points(unit, out)
