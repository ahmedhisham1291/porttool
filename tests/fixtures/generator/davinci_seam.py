"""Everything of the synthetic repo that is DaVinci-specific and therefore PROVISIONAL.

The AUTOSAR XML of the generator follows the standard. The files below do not: their
real formats are not known yet. Each function is one assumption to replace once
docs/discovery.md exists.

PENDING-DISCOVERY list (also in tests/fixtures/generator/HANDOFF notes of porttool/core):

* the repo layout (``Config/Developer``, ``Config/ECUC``, ``Appl/GenData``, ``Models/<Swc>/arxml``);
* the format of the ``.dcf`` workspace index (:func:`render_dcf`);
* the format of the ``.dpa`` project file (:func:`render_dpa`);
* the ECUC file: package name, definition path prefix and container naming (:func:`render_ecuc`);
* that a model-owned SWC has one copy in the workspace and one in its export folder.
"""

from __future__ import annotations

from tests.fixtures.generator import arxml
from tests.fixtures.generator.plan import ECUC_PACKAGE, Plan

WORKSPACE = "Config/Developer"
ECUC_DIR = "Config/ECUC"
GEN_DATA = "Appl/GenData"

_ROOT_ITEMS = (
    ("ComponentTypes/", "COMPONENTTYPE"),
    ("PortInterfaces/", "PORTINTERFACE"),
    ("DataTypes/", "DATATYPE"),
)


def dcf_path(plan: Plan) -> str:
    return f"{WORKSPACE}/{plan.spec.project_name}.dcf"


def dpa_path(plan: Plan) -> str:
    return f"Config/{plan.spec.project_name}.dpa"


def ecuc_path(plan: Plan) -> str:
    return f"{ECUC_DIR}/{plan.spec.project_name}_ecuc.arxml"


def render_dcf(plan: Plan, workspace_files: list[str]) -> str:
    """PENDING-DISCOVERY: a guessed .dcf format, an XML list of the workspace ARXML files
    relative to the .dcf folder, with backslashes."""
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        "<!-- PROVISIONAL synthetic file: the real .dcf format is PENDING-DISCOVERY. -->",
        '<DCF ARXMLVERSION="4.2.2">',
        f"  <NAME>{plan.spec.project_name}</NAME>",
    ]
    for file in workspace_files:
        relative = file.removeprefix(WORKSPACE + "/")
        item = next((item for prefix, item in _ROOT_ITEMS if relative.startswith(prefix)), "")
        windows = relative.replace("/", "\\")
        lines.append(f'  <FILEREF><ARXML ROOTITEM="{item}">{windows}</ARXML></FILEREF>')
    lines.append("</DCF>")
    return "\n".join(lines) + "\n"


def render_dpa(plan: Plan) -> str:
    """PENDING-DISCOVERY: a guessed .dpa format, XML with paths relative to the .dpa folder."""
    name = plan.spec.project_name
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<!-- PROVISIONAL synthetic file: the real .dpa format is PENDING-DISCOVERY. -->\n"
        "<Project>\n"
        f"  <Name>{name}</Name>\n"
        "  <Folders>\n"
        "    <ECUC>.\\ECUC</ECUC>\n"
        "    <GenData>..\\Appl\\GenData</GenData>\n"
        "  </Folders>\n"
        f"  <Developer><Workspace>.\\Developer\\{name}.dcf</Workspace></Developer>\n"
        f"  <ActiveEcuc>.\\ECUC\\{name}_ecuc.arxml</ActiveEcuc>\n"
        "</Project>\n"
    )


def _container(name: str, definition: str, body: str, pad: str) -> str:
    return (
        f"{pad}<ECUC-CONTAINER-VALUE>\n"
        f"{pad}  <SHORT-NAME>{name}</SHORT-NAME>\n"
        f'{pad}  <DEFINITION-REF DEST="ECUC-PARAM-CONF-CONTAINER-DEF">{definition}'
        f"</DEFINITION-REF>\n{body}{pad}</ECUC-CONTAINER-VALUE>\n"
    )


def _reference(definition: str, kind: str, dest: str, target: str, pad: str) -> str:
    return (
        f"{pad}<ECUC-REFERENCE-VALUE>\n"
        f'{pad}  <DEFINITION-REF DEST="{kind}">{definition}</DEFINITION-REF>\n'
        f'{pad}  <VALUE-REF DEST="{dest}">{target}</VALUE-REF>\n'
        f"{pad}</ECUC-REFERENCE-VALUE>\n"
    )


def _integer(definition: str, value: int, pad: str) -> str:
    return (
        f"{pad}<PARAMETER-VALUES>\n"
        f"{pad}  <ECUC-NUMERICAL-PARAM-VALUE>\n"
        f'{pad}    <DEFINITION-REF DEST="ECUC-INTEGER-PARAM-DEF">{definition}</DEFINITION-REF>\n'
        f"{pad}    <VALUE>{value}</VALUE>\n"
        f"{pad}  </ECUC-NUMERICAL-PARAM-VALUE>\n"
        f"{pad}</PARAMETER-VALUES>\n"
    )


def _module(name: str, root: str, containers: str) -> str:
    return (
        "<ECUC-MODULE-CONFIGURATION-VALUES>\n"
        f"  <SHORT-NAME>{name}</SHORT-NAME>\n"
        f'  <DEFINITION-REF DEST="ECUC-MODULE-DEF">{root}/{name}</DEFINITION-REF>\n'
        "  <IMPLEMENTATION-CONFIG-VARIANT>VARIANT-PRE-COMPILE</IMPLEMENTATION-CONFIG-VARIANT>\n"
        f"  <CONTAINERS>\n{containers}  </CONTAINERS>\n"
        "</ECUC-MODULE-CONFIGURATION-VALUES>\n"
    )


def render_ecuc(plan: Plan) -> str:
    """The ECUC value file: Os tasks and the Rte event-to-task mapping.

    The element structure is AUTOSAR standard (ECUC-MODULE-CONFIGURATION-VALUES with
    RteSwComponentInstance / RteEventToTaskMapping containers, RteEventRef,
    RteMappedToTaskRef, RtePositionInTask). PENDING-DISCOVERY: the package name, the
    definition path prefix (``spec.ecuc_definition_root``) and the container short names.
    """
    root = plan.spec.ecuc_definition_root
    tasks = "".join(
        _container(
            task,
            f"{root}/Os/OsTask",
            _integer(f"{root}/Os/OsTask/OsTaskPriority", priority, "      "),
            "    ",
        )
        for priority, task in enumerate(plan.tasks, start=1)
    )
    instance_def = f"{root}/Rte/RteSwComponentInstance"
    mapping_def = f"{instance_def}/RteEventToTaskMapping"
    instances = ""
    for swc in plan.swcs:
        mappings = ""
        for event in swc.events:
            if event.task is None or event.position is None:
                continue
            references = _reference(
                f"{mapping_def}/RteEventRef",
                "ECUC-FOREIGN-REFERENCE-DEF",
                arxml.EVENT_TAGS[event.kind],
                f"{swc.behavior_path}/{event.name}",
                "          ",
            ) + _reference(
                f"{mapping_def}/RteMappedToTaskRef",
                "ECUC-REFERENCE-DEF",
                "ECUC-CONTAINER-VALUE",
                f"{ECUC_PACKAGE}/Os/{event.task}",
                "          ",
            )
            body = (
                _integer(f"{mapping_def}/RtePositionInTask", event.position, "        ")
                + f"        <REFERENCE-VALUES>\n{references}        </REFERENCE-VALUES>\n"
            )
            mappings += _container(event.name, mapping_def, body, "      ")
        component = _reference(
            f"{instance_def}/RteSoftwareComponentInstanceRef",
            "ECUC-FOREIGN-REFERENCE-DEF",
            "SW-COMPONENT-PROTOTYPE",
            swc.prototype_path,
            "        ",
        )
        body = f"      <REFERENCE-VALUES>\n{component}      </REFERENCE-VALUES>\n"
        if mappings:
            body += f"      <SUB-CONTAINERS>\n{mappings}      </SUB-CONTAINERS>\n"
        instances += _container(swc.name, instance_def, body, "    ")
    return arxml.document(
        [
            (ECUC_PACKAGE, _module("Os", root, tasks)),
            (ECUC_PACKAGE, _module("Rte", root, instances)),
        ]
    )


def render_owners(plan: Plan) -> str:
    rows = ["SWC,team,arxml_owner,authoring_tool,export_folder"]
    for swc in plan.swcs:
        owner = "model" if swc.export_folder else "davinci"
        rows.append(
            f"{swc.name},{swc.team},{owner},{swc.authoring_tool or ''},{swc.export_folder or ''}"
        )
    return "\n".join(rows) + "\n"


def render_config(plan: Plan) -> str:
    name = plan.spec.project_name
    return (
        f"# Synthetic repo for PortTool tests (seed {plan.spec.seed}). Not a real project.\n"
        "format_version = 1\n\n[paths]\n"
        f'dpa_file = "{dpa_path(plan)}"\n'
        f'dcf_file = "{WORKSPACE}/{name}.dcf"\n'
        f'ecuc_file = "{ecuc_path(plan)}"\n'
        f'gen_data = "{GEN_DATA}"\n'
        f'arxml_roots = ["{WORKSPACE}"]\n'
        f'exclude_globs = ["{GEN_DATA}/**"]\n'
    )
