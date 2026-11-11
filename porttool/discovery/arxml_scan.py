"""One streaming pass over an ARXML file that collects every fact discovery needs.

The scanner follows the AUTOSAR 4.x standard XML only: packageable elements are the
children of ``AR-PACKAGE/ELEMENTS``; names are ``SHORT-NAME`` children. It keeps counts
and names, never values, and never builds a tree.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

from porttool.discovery.xmlfeed import XmlProblem, XmlSink, feed_xml
from porttool.discovery.xmlstyle import StyleCollector, StyleFacts

COMPOSITION_KIND = "COMPOSITION-SW-COMPONENT-TYPE"
ECUC_MODULE_KIND = "ECUC-MODULE-CONFIGURATION-VALUES"
IMPLEMENTATION_KIND = "SWC-IMPLEMENTATION"

COMPONENT_KINDS: frozenset[str] = frozenset(
    {
        "APPLICATION-SW-COMPONENT-TYPE",
        "SENSOR-ACTUATOR-SW-COMPONENT-TYPE",
        "COMPLEX-DEVICE-DRIVER-SW-COMPONENT-TYPE",
        "ECU-ABSTRACTION-SW-COMPONENT-TYPE",
        "SERVICE-SW-COMPONENT-TYPE",
        "SERVICE-PROXY-SW-COMPONENT-TYPE",
        "NV-BLOCK-SW-COMPONENT-TYPE",
        "PARAMETER-SW-COMPONENT-TYPE",
    }
)
INTERFACE_KINDS: frozenset[str] = frozenset(
    {
        "SENDER-RECEIVER-INTERFACE",
        "CLIENT-SERVER-INTERFACE",
        "MODE-SWITCH-INTERFACE",
        "PARAMETER-INTERFACE",
        "NV-DATA-INTERFACE",
        "TRIGGER-INTERFACE",
    }
)
DATA_TYPE_KINDS: frozenset[str] = frozenset(
    {
        "APPLICATION-PRIMITIVE-DATA-TYPE",
        "APPLICATION-ARRAY-DATA-TYPE",
        "APPLICATION-RECORD-DATA-TYPE",
        "IMPLEMENTATION-DATA-TYPE",
        "SW-BASE-TYPE",
        "COMPU-METHOD",
        "DATA-CONSTR",
        "UNIT",
        "PHYSICAL-DIMENSION",
        "DATA-TYPE-MAPPING-SET",
        "CONSTANT-SPECIFICATION",
        "MODE-DECLARATION-GROUP",
        "SW-ADDR-METHOD",
        "SW-RECORD-LAYOUT",
    }
)
ECUC_KINDS: frozenset[str] = frozenset(
    {ECUC_MODULE_KIND, "ECUC-VALUE-COLLECTION", "ECUC-MODULE-DEF", "ECUC-DEFINITION-COLLECTION"}
)
SYSTEM_KINDS: frozenset[str] = frozenset(
    {
        "SYSTEM",
        "FLAT-MAP",
        "ECU-INSTANCE",
        "SYSTEM-SIGNAL",
        "SYSTEM-SIGNAL-GROUP",
        "I-SIGNAL",
        "I-SIGNAL-GROUP",
        "I-SIGNAL-I-PDU",
        "CAN-CLUSTER",
        "LIN-CLUSTER",
        "FLEXRAY-CLUSTER",
        "ETHERNET-CLUSTER",
        "CAN-FRAME",
    }
)
BEHAVIOR_KINDS: frozenset[str] = frozenset(
    {IMPLEMENTATION_KIND, "BSW-MODULE-DESCRIPTION", "BSW-IMPLEMENTATION", "SWC-BSW-MAPPING"}
)

#: Tags below a top-level object that are reported per file.
NESTED_TAGS: tuple[str, ...] = (
    "P-PORT-PROTOTYPE",
    "R-PORT-PROTOTYPE",
    "PR-PORT-PROTOTYPE",
    "SWC-INTERNAL-BEHAVIOR",
    "RUNNABLE-ENTITY",
    "TIMING-EVENT",
    "DATA-RECEIVED-EVENT",
    "DATA-RECEIVE-ERROR-EVENT",
    "DATA-SEND-COMPLETED-EVENT",
    "OPERATION-INVOKED-EVENT",
    "SWC-MODE-SWITCH-EVENT",
    "INIT-EVENT",
    "BACKGROUND-EVENT",
    "SW-COMPONENT-PROTOTYPE",
    "ASSEMBLY-SW-CONNECTOR",
    "DELEGATION-SW-CONNECTOR",
    "ECUC-CONTAINER-VALUE",
    "ROOT-SW-COMPOSITION-PROTOTYPE",
    "VARIATION-POINT",
)

#: Names of authoring tools whose mention marks a file as exported from a model.
_TOOL_KEYWORD_RE = re.compile(
    r"simulink|matlab|embedded\s*coder|targetlink|dspace", re.IGNORECASE
)
_KEYWORD_NAMES = {
    "simulink": "Simulink",
    "matlab": "MATLAB",
    "embeddedcoder": "Embedded Coder",
    "targetlink": "TargetLink",
    "dspace": "dSPACE",
}

#: Elements whose text is documentation: the only places where tool names are searched.
_DOC_TAGS: frozenset[str] = frozenset(
    {"ADMIN-DATA", "DESC", "LONG-NAME", "INTRODUCTION", "ANNOTATIONS", "USED-CODE-GENERATOR"}
)
_TEXT_TAGS: frozenset[str] = frozenset(
    {
        "SHORT-NAME",
        "DEFINITION-REF",
        "VALUE-REF",
        "TYPE-TREF",
        "SOFTWARE-COMPOSITION-TREF",
        "BEHAVIOR-REF",
    }
)
_CONNECTOR_TAGS: frozenset[str] = frozenset(
    {"ASSEMBLY-SW-CONNECTOR", "DELEGATION-SW-CONNECTOR", "PASS-THROUGH-SW-CONNECTOR"}
)
_DETAIL_MODULES: frozenset[str] = frozenset({"rte", "os"})

MAX_OBJECTS_PER_FILE = 500
MAX_UUID_SAMPLES = 200
_HEX = frozenset("0123456789abcdefABCDEF")


def category_of(kind: str) -> str:
    """Group a top-level element tag into a readable category."""
    if kind == COMPOSITION_KIND:
        return "composition"
    if kind in COMPONENT_KINDS:
        return "component_type"
    if kind in INTERFACE_KINDS:
        return "port_interface"
    if kind in DATA_TYPE_KINDS:
        return "data_type"
    if kind in ECUC_KINDS:
        return "ecuc"
    if kind in SYSTEM_KINDS:
        return "system"
    if kind in BEHAVIOR_KINDS:
        return "implementation"
    return "other"


def find_tool_keywords(text: str) -> set[str]:
    """Canonical names of the authoring tools mentioned in ``text``."""
    found: set[str] = set()
    for match in _TOOL_KEYWORD_RE.finditer(text):
        key = re.sub(r"\s+", "", match.group(0).lower())
        found.add(_KEYWORD_NAMES.get(key, key))
    return found


def uuid_format(value: str) -> tuple[str, str]:
    """Classify a UUID attribute: (``8-4-4-4-12 lower``, version digit)."""
    body = value.strip()
    prefix = ""
    if body.startswith("{") and body.endswith("}"):
        prefix, body = "{}", body[1:-1]
    if ":" in body:
        head, _, body = body.partition(":")
        prefix += head + ":"
    groups = body.split("-")
    if not body or not all(group and all(c in _HEX for c in group) for group in groups):
        return "other", "?"
    letters = [char for char in body if char.isalpha()]
    if not letters:
        case = "digits"
    elif all(char.islower() for char in letters):
        case = "lower"
    elif all(char.isupper() for char in letters):
        case = "upper"
    else:
        case = "mixed"
    lengths = "-".join(str(len(group)) for group in groups)
    version = "?"
    if lengths == "8-4-4-4-12":
        version = groups[2][0].lower()
    elif lengths == "32":
        version = body[12].lower()
    return f"{prefix}{lengths} {case}", version


@dataclass(slots=True)
class TopObject:
    """One packageable element: a child of AR-PACKAGE/ELEMENTS."""

    kind: str
    package: str
    name: str
    sections: tuple[str, ...]

    @property
    def path(self) -> str:
        return f"{self.package.rstrip('/')}/{self.name}"


@dataclass(slots=True)
class CompositionFacts:
    path: str = ""
    prototypes: list[tuple[str, str, str]] = field(default_factory=list)  # name, type, DEST
    assembly: int = 0
    delegation: int = 0
    pass_through: int = 0


@dataclass(slots=True)
class EcucModuleFacts:
    """Structure of one ECUC module configuration. No short names, no values."""

    definition: str = ""
    containers: int = 0
    detailed: bool = False
    #: (owner tag, DEST of the DEFINITION-REF, definition path below the module) -> count
    entries: Counter[tuple[str, str, str]] = field(default_factory=Counter)
    #: (definition path below the module, DEST of the VALUE-REF) -> count
    value_ref_dests: Counter[tuple[str, str]] = field(default_factory=Counter)
    foreign_definitions: int = 0
    mapping_containers: int = 0
    mappings_with_task: int = 0
    mappings_with_event: int = 0
    referenced_tasks: set[str] = field(default_factory=set)

    @property
    def module(self) -> str:
        return self.definition.rpartition("/")[2]


@dataclass(slots=True)
class ArxmlFacts:
    """Everything one pass learned about one ARXML file."""

    rel: str
    size: int
    problem: XmlProblem | None = None
    style: StyleFacts | None = None
    root_tag: str = ""
    elements: int = 0
    tag_counts: dict[str, int] = field(default_factory=dict)
    kind_counts: Counter[str] = field(default_factory=Counter)
    objects: list[TopObject] = field(default_factory=list)
    objects_total: int = 0
    packages: Counter[tuple[str, str]] = field(default_factory=Counter)  # (kind, package)
    sections: Counter[tuple[str, str]] = field(default_factory=Counter)  # (kind, child tag)
    compositions: list[CompositionFacts] = field(default_factory=list)
    root_composition_refs: list[str] = field(default_factory=list)
    behavior_refs: list[str] = field(default_factory=list)  # from SWC-IMPLEMENTATION
    ecuc_modules: list[EcucModuleFacts] = field(default_factory=list)
    uuid_count: int = 0
    uuid_formats: Counter[str] = field(default_factory=Counter)
    uuid_versions: Counter[str] = field(default_factory=Counter)
    admin_data_parents: Counter[str] = field(default_factory=Counter)
    sdg_gids: Counter[str] = field(default_factory=Counter)
    sd_gids: Counter[str] = field(default_factory=Counter)
    mentions: Counter[tuple[str, str]] = field(default_factory=Counter)  # (tool, where)
    comments: int = 0
    self_closing: int = 0
    open_close_empty: int = 0

    @property
    def ok(self) -> bool:
        return self.problem is None

    @property
    def folder(self) -> str:
        return self.rel.rpartition("/")[0]


class _ArxmlSink(XmlSink):
    """Event receiver that fills an ``ArxmlFacts``. Hot path: keep it flat and cheap."""

    def __init__(self, facts: ArxmlFacts) -> None:
        super().__init__()
        self.facts = facts
        self._stack: list[str] = []
        self._tag_counts = facts.tag_counts
        self._last_start_depth = -1
        self._had_data = False
        self._text: list[str] | None = None
        self._last_dest = ""
        self._packages: list[str] = []
        # current top-level object
        self._obj_depth = -1
        self._section_depth = -1
        self._obj_kind = ""
        self._obj_name = ""
        self._obj_package = ""
        self._obj_sections: list[str] = []
        # documentation context
        self._doc_start = -1
        self._doc_tag = ""
        self._doc_buf: list[str] = []
        # composition
        self._comp: CompositionFacts | None = None
        self._proto_name = ""
        self._proto_type = ""
        self._proto_dest = ""
        # ECUC
        self._ecuc: EcucModuleFacts | None = None
        self._ecuc_depth = -1
        self._def_by_depth: dict[int, str] = {}
        self._map_depth = -1
        self._map_has_task = False
        self._map_has_event = False
        self._uuid_samples = 0

    # ---- events

    def start(self, name: str, attrs: list[str]) -> None:
        if ":" in name:
            name = name.rpartition(":")[2]
        stack = self._stack
        depth = len(stack)
        parent = stack[-1] if depth else ""
        stack.append(name)
        counts = self._tag_counts
        counts[name] = counts.get(name, 0) + 1
        self._last_start_depth = depth
        self._had_data = False
        if attrs:
            self._attributes(name, attrs)
        if depth == self._section_depth:
            self._obj_sections.append(name)
        elif parent == "ELEMENTS" and depth >= 2 and stack[-3] == "AR-PACKAGE":
            self._begin_object(name, depth)
        elif depth == 0:
            self.facts.root_tag = name
        if name == "AR-PACKAGE":
            self._packages.append("")
        elif name == "ADMIN-DATA":
            self.facts.admin_data_parents[parent] += 1
        elif name in _CONNECTOR_TAGS:
            self._connector(name)
        elif name == "SW-COMPONENT-PROTOTYPE":
            self._proto_name = self._proto_type = self._proto_dest = ""
        elif name == "ECUC-CONTAINER-VALUE" and self._ecuc is not None:
            self._ecuc.containers += 1
        if self._doc_start < 0 and name in _DOC_TAGS:
            self._doc_start = depth
            self._doc_tag = name
            self._doc_buf = []
        if self._ecuc is not None and self._ecuc.detailed:
            self._def_by_depth.pop(depth, None)
        if name in _TEXT_TAGS:
            self._text = []

    def end(self, name: str) -> None:
        if ":" in name:
            name = name.rpartition(":")[2]
        stack = self._stack
        depth = len(stack) - 1
        if self._last_start_depth == depth and not self._had_data:
            self._count_empty()
        buffer = self._text
        if buffer is not None:
            self._text = None
            self._leaf_text(name, stack[-2] if depth else "", "".join(buffer).strip(), depth)
        if depth == self._doc_start:
            self._end_doc()
        if depth == self._map_depth:
            self._end_mapping()
        if depth == self._obj_depth:
            self._end_object()
        if name == "AR-PACKAGE":
            if self._packages:
                self._packages.pop()
        elif name == "SW-COMPONENT-PROTOTYPE" and self._comp is not None:
            self._comp.prototypes.append((self._proto_name, self._proto_type, self._proto_dest))
        stack.pop()

    def data(self, text: str) -> None:
        self._had_data = True
        if self._text is not None:
            self._text.append(text)
        if self._doc_start >= 0:
            self._doc_buf.append(text)

    def comment(self, text: str) -> None:
        self._had_data = True
        self.facts.comments += 1
        for keyword in find_tool_keywords(text):
            self.facts.mentions[(keyword, "comment")] += 1

    # ---- details

    def _attributes(self, name: str, attrs: list[str]) -> None:
        facts = self.facts
        for index in range(0, len(attrs), 2):
            key = attrs[index]
            if key == "UUID":
                facts.uuid_count += 1
                if self._uuid_samples < MAX_UUID_SAMPLES:
                    self._uuid_samples += 1
                    label, version = uuid_format(attrs[index + 1])
                    facts.uuid_formats[label] += 1
                    facts.uuid_versions[version] += 1
            elif key == "DEST":
                self._last_dest = attrs[index + 1]
            elif key == "GID":
                if name == "SDG":
                    facts.sdg_gids[attrs[index + 1]] += 1
                elif name == "SD":
                    facts.sd_gids[attrs[index + 1]] += 1

    def _count_empty(self) -> None:
        parser = self.parser
        if parser is None:
            return
        before = self.bytes_before(parser.CurrentByteIndex)
        if before is None:
            return
        if before == b"/>":
            self.facts.self_closing += 1
        else:
            self.facts.open_close_empty += 1

    def _package_path(self) -> str:
        return "/" + "/".join(self._packages)

    def _begin_object(self, name: str, depth: int) -> None:
        self._obj_depth = depth
        self._section_depth = depth + 1
        self._obj_kind = name
        self._obj_name = ""
        self._obj_package = self._package_path()
        self._obj_sections = []
        if name == COMPOSITION_KIND:
            self._comp = CompositionFacts()
        elif name == ECUC_MODULE_KIND:
            self._ecuc = EcucModuleFacts()
            self._ecuc_depth = depth
            self._def_by_depth = {}

    def _end_object(self) -> None:
        facts = self.facts
        kind = self._obj_kind
        sections = tuple(dict.fromkeys(self._obj_sections))
        item = TopObject(kind, self._obj_package, self._obj_name, sections)
        facts.objects_total += 1
        facts.kind_counts[kind] += 1
        facts.packages[(kind, item.package)] += 1
        for section in sections:
            facts.sections[(kind, section)] += 1
        if len(facts.objects) < MAX_OBJECTS_PER_FILE:
            facts.objects.append(item)
        if self._comp is not None:
            self._comp.path = item.path
            facts.compositions.append(self._comp)
            self._comp = None
        if self._ecuc is not None:
            facts.ecuc_modules.append(self._ecuc)
            self._ecuc = None
            self._ecuc_depth = -1
        self._obj_depth = -1
        self._section_depth = -1

    def _connector(self, name: str) -> None:
        composition = self._comp
        if composition is None:
            return
        if name == "ASSEMBLY-SW-CONNECTOR":
            composition.assembly += 1
        elif name == "DELEGATION-SW-CONNECTOR":
            composition.delegation += 1
        else:
            composition.pass_through += 1

    def _leaf_text(self, name: str, parent: str, text: str, depth: int) -> None:
        if name == "SHORT-NAME":
            if parent == "AR-PACKAGE":
                if self._packages:
                    self._packages[-1] = text
            elif depth - 1 == self._obj_depth:
                self._obj_name = text
            elif parent == "SW-COMPONENT-PROTOTYPE":
                self._proto_name = text
        elif name == "DEFINITION-REF":
            self._definition_ref(parent, text, depth)
        elif name == "VALUE-REF":
            self._value_ref(text, depth)
        elif name == "TYPE-TREF":
            if parent == "SW-COMPONENT-PROTOTYPE":
                self._proto_type = text
                self._proto_dest = self._last_dest
        elif name == "SOFTWARE-COMPOSITION-TREF":
            self.facts.root_composition_refs.append(text)
        elif name == "BEHAVIOR-REF" and self._obj_kind == IMPLEMENTATION_KIND:
            self.facts.behavior_refs.append(text)

    def _definition_ref(self, parent: str, text: str, depth: int) -> None:
        module = self._ecuc
        if module is None:
            return
        if depth - 1 == self._ecuc_depth:
            module.definition = text
            module.detailed = module.module.lower() in _DETAIL_MODULES
            return
        if not module.detailed:
            return
        prefix = module.definition + "/"
        if text.startswith(prefix):
            suffix = text[len(prefix) :]
        else:
            suffix = text
            module.foreign_definitions += 1
        module.entries[(parent, self._last_dest, suffix)] += 1
        self._def_by_depth[depth - 1] = suffix
        if parent == "ECUC-CONTAINER-VALUE" and suffix.endswith("RteEventToTaskMapping"):
            if self._map_depth >= 0:
                self._end_mapping()
            module.mapping_containers += 1
            self._map_depth = depth - 1
            self._map_has_task = False
            self._map_has_event = False

    def _value_ref(self, text: str, depth: int) -> None:
        module = self._ecuc
        if module is None or not module.detailed:
            return
        suffix = self._def_by_depth.get(depth - 1, "")
        module.value_ref_dests[(suffix, self._last_dest)] += 1
        if self._map_depth >= 0 and text:
            if suffix.endswith("RteMappedToTaskRef"):
                self._map_has_task = True
                module.referenced_tasks.add(text)
            elif suffix.endswith("RteEventRef"):
                self._map_has_event = True

    def _end_mapping(self) -> None:
        module = self._ecuc
        if module is not None:
            module.mappings_with_task += self._map_has_task
            module.mappings_with_event += self._map_has_event
        self._map_depth = -1

    def _end_doc(self) -> None:
        text = "".join(self._doc_buf)
        self._doc_buf = []
        self._doc_start = -1
        for keyword in find_tool_keywords(text):
            self.facts.mentions[(keyword, self._doc_tag)] += 1


def scan_arxml(rel: str, size: int, chunks: Iterable[bytes]) -> ArxmlFacts:
    """Scan one ARXML document given as byte chunks.

    Never raises for bad XML or for a file that cannot be read: the problem is stored in
    ``facts.problem`` and the facts collected up to that point are kept.
    """
    facts = ArxmlFacts(rel=rel, size=size)
    sink = _ArxmlSink(facts)
    style = StyleCollector()
    try:
        facts.problem = feed_xml(chunks, sink, style)
    except OSError as error:
        facts.problem = XmlProblem("unreadable", type(error).__name__)
    facts.style = style.result()
    facts.elements = sum(facts.tag_counts.values())
    return facts
