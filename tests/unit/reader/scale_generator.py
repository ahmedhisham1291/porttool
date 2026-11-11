"""Throw-away generator of a large synthetic ECU repository (string templates only).

Used by ``bench_scale.py`` to measure the reader and by one slow test. It is NOT the
reference for correctness: the hand-written samples in ``samples.py`` are.

Default shape: about 2,000 ARXML files, 300 SWCs, 20,000 ports (5,000 provide ports, each
connected to three require ports) and 15,000 assembly connectors in one composition, one
runnable access per port, an ECUC file with tasks and one task mapping per timing event.
``target_bytes`` pads the repository to a total size with content the reader has to
parse but does not need (ADMIN-DATA on objects, system signals, unrelated ECUC modules),
at the element density of real ARXML.
"""

# ruff: noqa: E501 - XML templates are kept one element per line

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from porttool.core.config import ProjectConfig

NS = "http://autosar.org/schema/r4.0"
_HEAD = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    f'<AUTOSAR xmlns="{NS}" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
    f'xsi:schemaLocation="{NS} AUTOSAR_4-2-2.xsd">\n  <AR-PACKAGES>\n'
)
_TAIL = "  </AR-PACKAGES>\n</AUTOSAR>\n"

_ADMIN = (
    '<ADMIN-DATA><SDGS><SDG GID="DV:DEV"><SD GID="DV:Created">2024-01-01T00:00:00</SD>'
    '<SD GID="DV:Changed">2024-06-01T12:00:00</SD><SD GID="DV:Owner">generator</SD>'
    "</SDG></SDGS></ADMIN-DATA>"
)


@dataclass(frozen=True, slots=True)
class ScaleRepo:
    root: Path
    config: ProjectConfig
    files: int
    total_bytes: int
    swcs: int
    ports: int
    connectors: int
    interfaces: int
    runnables: int
    access_points: int
    triggers: int
    data_types: int
    tasks: int
    task_mappings: int
    one_swc_file: Path  # a file the benchmark modifies for the "one file changed" load


def _uuid(kind: int, index: int) -> str:
    return f"{kind:08x}-0000-4000-8000-{index:012x}"


def _package(name: str, body: str) -> str:
    return (
        f"    <AR-PACKAGE>\n      <SHORT-NAME>{name}</SHORT-NAME>\n      <ELEMENTS>\n{body}"
        "      </ELEMENTS>\n    </AR-PACKAGE>\n"
    )


def _signals(prefix: str, count: int) -> str:
    """Content the reader skips: about 480 bytes and 10 elements per signal."""
    return "".join(
        f'        <SYSTEM-SIGNAL UUID="{_uuid(9, index)}">\n'
        f"          <SHORT-NAME>{prefix}_Sig{index}</SHORT-NAME>\n"
        f'          <LONG-NAME><L-4 L="EN">Signal {index} of {prefix}</L-4></LONG-NAME>\n'
        "          <DYNAMIC-LENGTH>false</DYNAMIC-LENGTH>\n"
        "          <PHYSICAL-PROPS><SW-DATA-DEF-PROPS-VARIANTS><SW-DATA-DEF-PROPS-CONDITIONAL>\n"
        f'            <COMPU-METHOD-REF DEST="COMPU-METHOD">/DataTypes/Compu/CM_{index % 500}'
        "</COMPU-METHOD-REF>\n"
        "          </SW-DATA-DEF-PROPS-CONDITIONAL></SW-DATA-DEF-PROPS-VARIANTS></PHYSICAL-PROPS>\n"
        "        </SYSTEM-SIGNAL>\n"
        for index in range(count)
    )


def _write(path: Path, packages: str, pad_bytes: int = 0) -> int:
    text = _HEAD + packages
    if pad_bytes > 0:
        text += _package("Filler_" + path.stem, _signals(path.stem, max(1, pad_bytes // 480)))
    text += _TAIL
    data = text.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return len(data)


def _interface(index: int) -> str:
    return (
        f'        <SENDER-RECEIVER-INTERFACE UUID="{_uuid(2, index)}">\n'
        f"          <SHORT-NAME>If_{index}</SHORT-NAME>\n          {_ADMIN}\n"
        "          <IS-SERVICE>false</IS-SERVICE>\n          <DATA-ELEMENTS>\n"
        "            <VARIABLE-DATA-PROTOTYPE>\n              <SHORT-NAME>Value</SHORT-NAME>\n"
        "              <SW-DATA-DEF-PROPS><SW-DATA-DEF-PROPS-VARIANTS>"
        "<SW-DATA-DEF-PROPS-CONDITIONAL><SW-IMPL-POLICY>STANDARD</SW-IMPL-POLICY>"
        "</SW-DATA-DEF-PROPS-CONDITIONAL></SW-DATA-DEF-PROPS-VARIANTS></SW-DATA-DEF-PROPS>\n"
        f'              <TYPE-TREF DEST="IMPLEMENTATION-DATA-TYPE">/DataTypes/T_{index % 3000}'
        "</TYPE-TREF>\n            </VARIABLE-DATA-PROTOTYPE>\n          </DATA-ELEMENTS>\n"
        "        </SENDER-RECEIVER-INTERFACE>\n"
    )


def _data_type(index: int) -> str:
    return (
        f'        <IMPLEMENTATION-DATA-TYPE UUID="{_uuid(3, index)}">\n'
        f"          <SHORT-NAME>T_{index}</SHORT-NAME>\n          <CATEGORY>VALUE</CATEGORY>\n"
        "          <SW-DATA-DEF-PROPS><SW-DATA-DEF-PROPS-VARIANTS><SW-DATA-DEF-PROPS-CONDITIONAL>\n"
        '            <BASE-TYPE-REF DEST="SW-BASE-TYPE">/DataTypes/Base/uint16</BASE-TYPE-REF>\n'
        f'            <COMPU-METHOD-REF DEST="COMPU-METHOD">/DataTypes/Compu/CM_{index % 500}'
        "</COMPU-METHOD-REF>\n"
        "          </SW-DATA-DEF-PROPS-CONDITIONAL></SW-DATA-DEF-PROPS-VARIANTS>"
        "</SW-DATA-DEF-PROPS>\n        </IMPLEMENTATION-DATA-TYPE>\n"
    )


def _compu_method(index: int) -> str:
    return (
        f"        <COMPU-METHOD>\n          <SHORT-NAME>CM_{index}</SHORT-NAME>\n"
        "          <CATEGORY>LINEAR</CATEGORY>\n          <COMPU-INTERNAL-TO-PHYS><COMPU-SCALES>"
        "<COMPU-SCALE><COMPU-RATIONAL-COEFFS><COMPU-NUMERATOR><V>0</V><V>0.5</V>"
        "</COMPU-NUMERATOR><COMPU-DENOMINATOR><V>1</V></COMPU-DENOMINATOR>"
        "</COMPU-RATIONAL-COEFFS></COMPU-SCALE></COMPU-SCALES></COMPU-INTERNAL-TO-PHYS>\n"
        "        </COMPU-METHOD>\n"
    )


def _port(swc: str, name: str, provide: bool, interface: int, uuid: str) -> str:
    tag = "P-PORT-PROTOTYPE" if provide else "R-PORT-PROTOTYPE"
    specs = "PROVIDED-COM-SPECS" if provide else "REQUIRED-COM-SPECS"
    spec = "NONQUEUED-SENDER-COM-SPEC" if provide else "NONQUEUED-RECEIVER-COM-SPEC"
    tref = "PROVIDED-INTERFACE-TREF" if provide else "REQUIRED-INTERFACE-TREF"
    return (
        f'            <{tag} UUID="{uuid}">\n              <SHORT-NAME>{name}</SHORT-NAME>\n'
        f"              {_ADMIN}\n              <{specs}>\n                <{spec}>\n"
        '                  <DATA-ELEMENT-REF DEST="VARIABLE-DATA-PROTOTYPE">'
        f"/PortInterfaces/If_{interface}/Value</DATA-ELEMENT-REF>\n"
        "                  <INIT-VALUE><NUMERICAL-VALUE-SPECIFICATION><VALUE>0</VALUE>"
        f"</NUMERICAL-VALUE-SPECIFICATION></INIT-VALUE>\n                </{spec}>\n"
        f"              </{specs}>\n"
        f'              <{tref} DEST="SENDER-RECEIVER-INTERFACE">/PortInterfaces/If_{interface}'
        f"</{tref}>\n            </{tag}>\n"
    )


def _access(swc: str, name: str, provide: bool, interface: int) -> str:
    dest = "P-PORT-PROTOTYPE" if provide else "R-PORT-PROTOTYPE"
    return (
        f"                    <VARIABLE-ACCESS>\n"
        f"                      <SHORT-NAME>{'DWA' if provide else 'DRA'}_{name}_Value</SHORT-NAME>\n"
        "                      <ACCESSED-VARIABLE><AUTOSAR-VARIABLE-IREF>\n"
        f'                        <PORT-PROTOTYPE-REF DEST="{dest}">/ComponentTypes/{swc}/{name}'
        "</PORT-PROTOTYPE-REF>\n"
        '                        <TARGET-DATA-PROTOTYPE-REF DEST="VARIABLE-DATA-PROTOTYPE">'
        f"/PortInterfaces/If_{interface}/Value</TARGET-DATA-PROTOTYPE-REF>\n"
        "                      </AUTOSAR-VARIABLE-IREF></ACCESSED-VARIABLE>\n"
        "                    </VARIABLE-ACCESS>\n"
    )


def _connector(index: int, p_swc: str, p_port: str, r_swc: str, r_port: str) -> str:
    return (
        f'            <ASSEMBLY-SW-CONNECTOR UUID="{_uuid(6, index)}">\n'
        f"              <SHORT-NAME>{p_swc}_{p_port}_{r_swc}_{r_port}</SHORT-NAME>\n"
        "              <PROVIDER-IREF>\n"
        '                <CONTEXT-COMPONENT-REF DEST="SW-COMPONENT-PROTOTYPE">'
        f"/ComponentTypes/EcuComposition/{p_swc}</CONTEXT-COMPONENT-REF>\n"
        f'                <TARGET-P-PORT-REF DEST="P-PORT-PROTOTYPE">/ComponentTypes/{p_swc}/{p_port}'
        "</TARGET-P-PORT-REF>\n              </PROVIDER-IREF>\n              <REQUESTER-IREF>\n"
        '                <CONTEXT-COMPONENT-REF DEST="SW-COMPONENT-PROTOTYPE">'
        f"/ComponentTypes/EcuComposition/{r_swc}</CONTEXT-COMPONENT-REF>\n"
        f'                <TARGET-R-PORT-REF DEST="R-PORT-PROTOTYPE">/ComponentTypes/{r_swc}/{r_port}'
        "</TARGET-R-PORT-REF>\n              </REQUESTER-IREF>\n"
        "            </ASSEMBLY-SW-CONNECTOR>\n"
    )


def _ecuc_container(name: str, definition: str, inner: str = "") -> str:
    return (
        f"            <ECUC-CONTAINER-VALUE>\n              <SHORT-NAME>{name}</SHORT-NAME>\n"
        f'              <DEFINITION-REF DEST="ECUC-PARAM-CONF-CONTAINER-DEF">{definition}'
        f"</DEFINITION-REF>\n{inner}            </ECUC-CONTAINER-VALUE>\n"
    )


def _ecuc_param(definition: str, value: object) -> str:
    return (
        "              <PARAMETER-VALUES><ECUC-NUMERICAL-PARAM-VALUE>"
        f'<DEFINITION-REF DEST="ECUC-INTEGER-PARAM-DEF">{definition}</DEFINITION-REF>'
        f"<VALUE>{value}</VALUE></ECUC-NUMERICAL-PARAM-VALUE></PARAMETER-VALUES>\n"
    )


def generate(
    root: Path,
    *,
    swcs: int = 300,
    provide_ports: int = 5000,
    fan_out: int = 3,
    interfaces: int = 2000,
    data_types: int = 3000,
    runnables_per_swc: int = 8,
    tasks: int = 20,
    interface_files: int = 1000,
    data_type_files: int = 600,
    filler_files: int = 97,
    target_bytes: int = 0,
    cache_dir: Path | None = None,
) -> ScaleRepo:
    """Write the repository below ``root`` and return its configuration and exact counts."""
    developer = root / "Config" / "Developer"
    require_ports = provide_ports * fan_out
    total_bytes = 0
    files = 0

    # which ports each SWC owns: (port name, is provide port, interface index)
    ports_of: list[list[tuple[str, bool, int]]] = [[] for _ in range(swcs)]
    provider: list[tuple[int, str]] = []
    for p in range(provide_ports):
        owner = p % swcs
        name = f"Pp_{p}"
        ports_of[owner].append((name, True, p % interfaces))
        provider.append((owner, name))
    connectors: list[str] = []
    for r in range(require_ports):
        owner = (r * 7 + 1) % swcs
        name = f"Rp_{r}"
        p = r % provide_ports
        ports_of[owner].append((name, False, p % interfaces))
        p_owner, p_name = provider[p]
        connectors.append(_connector(r, f"Swc_{p_owner}", p_name, f"Swc_{owner}", name))

    # padding: spread over every file, in proportion
    natural_estimate = (
        (provide_ports + require_ports) * 1750
        + len(connectors) * 760
        + interfaces * 1000
        + data_types * 560
        + swcs * runnables_per_swc * 900
    )
    file_count_estimate = swcs + interface_files + data_type_files + filler_files + 3
    pad_total = max(0, target_bytes - natural_estimate)
    ecuc_pad = pad_total // 10  # one large ECUC file, like a real project
    pad_per_file = (pad_total - ecuc_pad) // max(1, file_count_estimate - 1)

    # ---- data types
    per_file = -(-data_types // data_type_files)
    for number in range(data_type_files):
        body = "".join(
            _data_type(i)
            for i in range(number * per_file, min(data_types, (number + 1) * per_file))
        )
        if not body:
            break
        total_bytes += _write(
            developer / "DataTypes" / f"Types_{number:04d}.arxml",
            _package("DataTypes", body),
            pad_per_file,
        )
        files += 1
    compu = "".join(_compu_method(i) for i in range(500))
    base = (
        "        <SW-BASE-TYPE>\n          <SHORT-NAME>uint16</SHORT-NAME>\n"
        "          <BASE-TYPE-SIZE>16</BASE-TYPE-SIZE>\n"
        "          <BASE-TYPE-ENCODING>NONE</BASE-TYPE-ENCODING>\n        </SW-BASE-TYPE>\n"
    )
    nested = (
        "    <AR-PACKAGE>\n      <SHORT-NAME>DataTypes</SHORT-NAME>\n      <AR-PACKAGES>\n"
        + _package("Compu", compu).replace("\n", "\n    ")
        + _package("Base", base).replace("\n", "\n    ")
        + "\n      </AR-PACKAGES>\n    </AR-PACKAGE>\n"
    )
    total_bytes += _write(developer / "DataTypes" / "Common.arxml", nested, pad_per_file)
    files += 1

    # ---- interfaces
    per_file = -(-interfaces // interface_files)
    for number in range(interface_files):
        body = "".join(
            _interface(i)
            for i in range(number * per_file, min(interfaces, (number + 1) * per_file))
        )
        if not body:
            break
        total_bytes += _write(
            developer / "PortInterfaces" / f"Interfaces_{number:04d}.arxml",
            _package("PortInterfaces", body),
            pad_per_file,
        )
        files += 1

    # ---- components
    runnable_total = access_total = trigger_total = 0
    mappings: list[str] = []
    one_swc_file = developer / "ComponentTypes" / "Swc_0.arxml"
    for s in range(swcs):
        swc = f"Swc_{s}"
        behavior = f"/ComponentTypes/{swc}/{swc}_InternalBehavior"
        own_ports = ports_of[s]
        port_xml = "".join(
            _port(swc, name, provide, interface, _uuid(4, s * 100000 + k))
            for k, (name, provide, interface) in enumerate(own_ports)
        )
        runnable_xml = ""
        event_xml = ""
        instance_mappings = ""
        for run in range(runnables_per_swc):
            mine = own_ports[run::runnables_per_swc]
            writes = "".join(_access(swc, n, True, i) for n, provide, i in mine if provide)
            reads = "".join(_access(swc, n, False, i) for n, provide, i in mine if not provide)
            access_total += len(mine)
            runnable_xml += (
                f'                <RUNNABLE-ENTITY UUID="{_uuid(5, s * 100 + run)}">\n'
                f"                  <SHORT-NAME>Run_{run}</SHORT-NAME>\n"
                "                  <MINIMUM-START-INTERVAL>0</MINIMUM-START-INTERVAL>\n"
                "                  <CAN-BE-INVOKED-CONCURRENTLY>false</CAN-BE-INVOKED-CONCURRENTLY>\n"
                + (
                    f"                  <DATA-READ-ACCESSS>\n{reads}                  </DATA-READ-ACCESSS>\n"
                    if reads
                    else ""
                )
                + (
                    f"                  <DATA-WRITE-ACCESSS>\n{writes}                  </DATA-WRITE-ACCESSS>\n"
                    if writes
                    else ""
                )
                + f"                  <SYMBOL>{swc}_Run_{run}</SYMBOL>\n"
                "                </RUNNABLE-ENTITY>\n"
            )
            event_xml += (
                f'                <TIMING-EVENT UUID="{_uuid(7, s * 100 + run)}">\n'
                f"                  <SHORT-NAME>TMT_Run_{run}</SHORT-NAME>\n"
                f'                  <START-ON-EVENT-REF DEST="RUNNABLE-ENTITY">{behavior}/Run_{run}'
                "</START-ON-EVENT-REF>\n"
                f"                  <PERIOD>{0.001 * (run + 1):.3f}</PERIOD>\n"
                "                </TIMING-EVENT>\n"
            )
            definition = "/MICROSAR/Rte/RteSwComponentInstance/RteEventToTaskMapping"
            instance_mappings += _ecuc_container(
                f"TMT_Run_{run}",
                definition,
                _ecuc_param(f"{definition}/RtePositionInTask", s * runnables_per_swc + run)
                + "              <REFERENCE-VALUES>\n"
                "                <ECUC-REFERENCE-VALUE>"
                f'<DEFINITION-REF DEST="ECUC-FOREIGN-REFERENCE-DEF">{definition}/RteEventRef'
                f'</DEFINITION-REF><VALUE-REF DEST="TIMING-EVENT">{behavior}/TMT_Run_{run}'
                "</VALUE-REF></ECUC-REFERENCE-VALUE>\n"
                "                <ECUC-REFERENCE-VALUE>"
                f'<DEFINITION-REF DEST="ECUC-REFERENCE-DEF">{definition}/RteMappedToTaskRef'
                '</DEFINITION-REF><VALUE-REF DEST="ECUC-CONTAINER-VALUE">'
                f"/ActiveEcuC/Os/Task_{(s + run) % tasks}</VALUE-REF></ECUC-REFERENCE-VALUE>\n"
                "              </REFERENCE-VALUES>\n",
            )
        runnable_total += runnables_per_swc
        trigger_total += runnables_per_swc
        mappings.append(
            _ecuc_container(
                swc,
                "/MICROSAR/Rte/RteSwComponentInstance",
                f"              <SUB-CONTAINERS>\n{instance_mappings}              </SUB-CONTAINERS>\n",
            )
        )
        body = (
            f'        <APPLICATION-SW-COMPONENT-TYPE UUID="{_uuid(1, s)}">\n'
            f"          <SHORT-NAME>{swc}</SHORT-NAME>\n          {_ADMIN}\n"
            f"          <PORTS>\n{port_xml}          </PORTS>\n"
            "          <INTERNAL-BEHAVIORS>\n            <SWC-INTERNAL-BEHAVIOR>\n"
            f"              <SHORT-NAME>{swc}_InternalBehavior</SHORT-NAME>\n"
            f"              <EVENTS>\n{event_xml}              </EVENTS>\n"
            f"              <RUNNABLES>\n{runnable_xml}              </RUNNABLES>\n"
            "            </SWC-INTERNAL-BEHAVIOR>\n          </INTERNAL-BEHAVIORS>\n"
            "        </APPLICATION-SW-COMPONENT-TYPE>\n"
        )
        total_bytes += _write(
            developer / "ComponentTypes" / f"{swc}.arxml",
            _package("ComponentTypes", body),
            pad_per_file,
        )
        files += 1

    # ---- the composition: every SWC once, every connector
    prototypes = "".join(
        f'            <SW-COMPONENT-PROTOTYPE UUID="{_uuid(8, s)}">\n'
        f"              <SHORT-NAME>Swc_{s}</SHORT-NAME>\n"
        f'              <TYPE-TREF DEST="APPLICATION-SW-COMPONENT-TYPE">/ComponentTypes/Swc_{s}'
        "</TYPE-TREF>\n            </SW-COMPONENT-PROTOTYPE>\n"
        for s in range(swcs)
    )
    body = (
        "        <COMPOSITION-SW-COMPONENT-TYPE>\n          <SHORT-NAME>EcuComposition</SHORT-NAME>\n"
        f"          <COMPONENTS>\n{prototypes}          </COMPONENTS>\n"
        f"          <CONNECTORS>\n{''.join(connectors)}          </CONNECTORS>\n"
        "        </COMPOSITION-SW-COMPONENT-TYPE>\n"
    )
    total_bytes += _write(
        developer / "Compositions" / "EcuComposition.arxml", _package("ComponentTypes", body)
    )
    files += 1

    # ---- other content inside the roots that the reader has to parse and skip
    for number in range(filler_files):
        total_bytes += _write(
            developer / "System" / f"Signals_{number:03d}.arxml", "", max(pad_per_file, 20_000)
        )
        files += 1

    # ---- ECUC: Os tasks, Rte task mappings, and unrelated modules as padding
    os_tasks = "".join(
        _ecuc_container(
            f"Task_{t}", "/MICROSAR/Os/OsTask", _ecuc_param("/MICROSAR/Os/OsTask/OsTaskPriority", t)
        )
        for t in range(tasks)
    )
    modules = [("Os", os_tasks), ("Rte", "".join(mappings))]
    unrelated = max(0, ecuc_pad) // 420
    for number in range(8):
        modules.append(
            (
                f"Com{number}",
                "".join(
                    _ecuc_container(
                        f"Signal_{number}_{i}",
                        "/MICROSAR/Com/ComConfig/ComSignal",
                        _ecuc_param("/MICROSAR/Com/ComConfig/ComSignal/ComBitPosition", i),
                    )
                    for i in range(unrelated // 8)
                ),
            )
        )
    ecuc_body = "".join(
        f"        <ECUC-MODULE-CONFIGURATION-VALUES>\n          <SHORT-NAME>{name}</SHORT-NAME>\n"
        f'          <DEFINITION-REF DEST="ECUC-MODULE-DEF">/MICROSAR/{name.rstrip("0123456789")}'
        f"</DEFINITION-REF>\n          <CONTAINERS>\n{containers}          </CONTAINERS>\n"
        "        </ECUC-MODULE-CONFIGURATION-VALUES>\n"
        for name, containers in modules
    )
    ecuc_file = root / "Config" / "ECUC" / "Ecu_ecuc.arxml"
    total_bytes += _write(ecuc_file, _package("ActiveEcuC", ecuc_body))
    files += 1

    config = ProjectConfig(
        repo_root=root,
        arxml_roots=(developer,),
        ecuc_file=ecuc_file,
        cache_dir=cache_dir,
    )
    return ScaleRepo(
        root=root,
        config=config,
        files=files,
        total_bytes=total_bytes,
        swcs=swcs,
        ports=provide_ports + require_ports,
        connectors=len(connectors),
        interfaces=interfaces,
        runnables=runnable_total,
        access_points=access_total,
        triggers=trigger_total,
        data_types=data_types,
        tasks=tasks,
        task_mappings=trigger_total,
        one_swc_file=one_swc_file,
    )
