"""Hand-written ARXML for the reader tests.

Everything here is written out by hand on purpose: these samples are the reference the
reader is checked against, so they must not come from the scale generator.

``write_sample_repo`` writes the reference repository described below. The small builder
functions (``doc``, ``package``, ``swc`` ...) are for corner-case tests that need one
tiny file.

Reference repository (all under Config/Developer unless noted):

    DataTypes/DataTypes.arxml       base types, data types, compu methods, constraint, unit,
                                    mapping set; package /Constants with one constant
    PortInterfaces/Interfaces.arxml 8 interfaces (all six kinds) and one mode declaration group
    ComponentTypes/SwcA.arxml       application SWC: 6 ports (one PR), 2 runnables, 4 events
    ComponentTypes/SwcB.arxml       application SWC: 3 ports, 3 runnables, 4 events
    ComponentTypes/SwcM.arxml       application SWC without internal behavior, 1 port
    ComponentTypes/OtherKinds.arxml one SWC of every other atomic kind
    Compositions/Compositions.arxml EcuComposition (4 prototypes, 4 assembly connectors) and
                                    Cluster (1 outer port, SwcB twice, 2 delegation connectors)
    Config/ECUC/Ecu_ecuc.arxml      3 OS tasks, task mappings (ECUC file, outside the roots)
"""

# ruff: noqa: E501 - the XML is written out by hand and kept one element per line

from __future__ import annotations

from pathlib import Path

NS = "http://autosar.org/schema/r4.0"
XSI = "http://www.w3.org/2001/XMLSchema-instance"

CT = "/ComponentTypes"
IF = "/PortInterfaces"
DT = "/DataTypes"
ECU = f"{CT}/EcuComposition"
CLUSTER = f"{CT}/Cluster"
BEH_A = f"{CT}/SwcA/SwcA_InternalBehavior"
BEH_B = f"{CT}/SwcB/SwcB_InternalBehavior"

F_TYPES = "Config/Developer/DataTypes/DataTypes.arxml"
F_INTERFACES = "Config/Developer/PortInterfaces/Interfaces.arxml"
F_SWC_A = "Config/Developer/ComponentTypes/SwcA.arxml"
F_SWC_B = "Config/Developer/ComponentTypes/SwcB.arxml"
F_SWC_M = "Config/Developer/ComponentTypes/SwcM.arxml"
F_OTHER = "Config/Developer/ComponentTypes/OtherKinds.arxml"
F_COMPOSITIONS = "Config/Developer/Compositions/Compositions.arxml"
F_ECUC = "Config/ECUC/Ecu_ecuc.arxml"


# --------------------------------------------------------------------------- builders


def doc(*packages: str, schema: str = "AUTOSAR_4-2-2.xsd", namespace: str = NS) -> str:
    """A complete ARXML document around the given AR-PACKAGE elements."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<AUTOSAR xmlns="{namespace}" xmlns:xsi="{XSI}" '
        f'xsi:schemaLocation="{namespace} {schema}">\n'
        "  <AR-PACKAGES>\n" + "\n".join(packages) + "\n  </AR-PACKAGES>\n</AUTOSAR>\n"
    )


def package(name: str, *elements: str, packages: str = "", extra: str = "") -> str:
    """An AR-PACKAGE with elements, optional sub-packages and extra children."""
    inner = f"<SHORT-NAME>{name}</SHORT-NAME>{extra}"
    if elements:
        inner += "<ELEMENTS>" + "".join(elements) + "</ELEMENTS>"
    if packages:
        inner += f"<AR-PACKAGES>{packages}</AR-PACKAGES>"
    return f"<AR-PACKAGE>{inner}</AR-PACKAGE>"


def swc(
    name: str,
    ports: str = "",
    behavior: str = "",
    kind: str = "APPLICATION-SW-COMPONENT-TYPE",
    extra: str = "",
) -> str:
    inner = f"<SHORT-NAME>{name}</SHORT-NAME>{extra}"
    if ports:
        inner += f"<PORTS>{ports}</PORTS>"
    if behavior:
        inner += f"<INTERNAL-BEHAVIORS>{behavior}</INTERNAL-BEHAVIORS>"
    return f"<{kind}>{inner}</{kind}>"


def p_port(
    name: str, interface: str, dest: str = "SENDER-RECEIVER-INTERFACE", inner: str = ""
) -> str:
    return (
        f"<P-PORT-PROTOTYPE><SHORT-NAME>{name}</SHORT-NAME>{inner}"
        f'<PROVIDED-INTERFACE-TREF DEST="{dest}">{interface}</PROVIDED-INTERFACE-TREF>'
        "</P-PORT-PROTOTYPE>"
    )


def r_port(
    name: str, interface: str, dest: str = "SENDER-RECEIVER-INTERFACE", inner: str = ""
) -> str:
    return (
        f"<R-PORT-PROTOTYPE><SHORT-NAME>{name}</SHORT-NAME>{inner}"
        f'<REQUIRED-INTERFACE-TREF DEST="{dest}">{interface}</REQUIRED-INTERFACE-TREF>'
        "</R-PORT-PROTOTYPE>"
    )


def sr_interface(name: str, element: str = "Value", type_path: str = f"{DT}/uint16") -> str:
    return (
        f"<SENDER-RECEIVER-INTERFACE><SHORT-NAME>{name}</SHORT-NAME><DATA-ELEMENTS>"
        f"<VARIABLE-DATA-PROTOTYPE><SHORT-NAME>{element}</SHORT-NAME>"
        f'<TYPE-TREF DEST="IMPLEMENTATION-DATA-TYPE">{type_path}</TYPE-TREF>'
        "</VARIABLE-DATA-PROTOTYPE></DATA-ELEMENTS></SENDER-RECEIVER-INTERFACE>"
    )


def implementation_type(name: str = "uint16") -> str:
    return (
        f"<IMPLEMENTATION-DATA-TYPE><SHORT-NAME>{name}</SHORT-NAME>"
        "<CATEGORY>VALUE</CATEGORY></IMPLEMENTATION-DATA-TYPE>"
    )


def composition(name: str, prototypes: str = "", connectors: str = "", ports: str = "") -> str:
    inner = f"<SHORT-NAME>{name}</SHORT-NAME>"
    if ports:
        inner += f"<PORTS>{ports}</PORTS>"
    if prototypes:
        inner += f"<COMPONENTS>{prototypes}</COMPONENTS>"
    if connectors:
        inner += f"<CONNECTORS>{connectors}</CONNECTORS>"
    return f"<COMPOSITION-SW-COMPONENT-TYPE>{inner}</COMPOSITION-SW-COMPONENT-TYPE>"


def prototype(name: str, type_path: str, dest: str = "APPLICATION-SW-COMPONENT-TYPE") -> str:
    return (
        f"<SW-COMPONENT-PROTOTYPE><SHORT-NAME>{name}</SHORT-NAME>"
        f'<TYPE-TREF DEST="{dest}">{type_path}</TYPE-TREF></SW-COMPONENT-PROTOTYPE>'
    )


def assembly(name: str, p_proto: str, p_port_path: str, r_proto: str, r_port_path: str) -> str:
    return (
        f"<ASSEMBLY-SW-CONNECTOR><SHORT-NAME>{name}</SHORT-NAME>"
        "<PROVIDER-IREF>"
        f'<CONTEXT-COMPONENT-REF DEST="SW-COMPONENT-PROTOTYPE">{p_proto}</CONTEXT-COMPONENT-REF>'
        f'<TARGET-P-PORT-REF DEST="P-PORT-PROTOTYPE">{p_port_path}</TARGET-P-PORT-REF>'
        "</PROVIDER-IREF><REQUESTER-IREF>"
        f'<CONTEXT-COMPONENT-REF DEST="SW-COMPONENT-PROTOTYPE">{r_proto}</CONTEXT-COMPONENT-REF>'
        f'<TARGET-R-PORT-REF DEST="R-PORT-PROTOTYPE">{r_port_path}</TARGET-R-PORT-REF>'
        "</REQUESTER-IREF></ASSEMBLY-SW-CONNECTOR>"
    )


def write(root: Path, rel: str, text: str) -> Path:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(text.encode("utf-8"))
    return target


# --------------------------------------------------------------------------- reference repo

DATA_TYPES = f"""<?xml version="1.0" encoding="UTF-8"?>
<AUTOSAR xmlns="{NS}" xmlns:xsi="{XSI}" xsi:schemaLocation="{NS} AUTOSAR_4-2-2.xsd">
  <AR-PACKAGES>
    <AR-PACKAGE>
      <SHORT-NAME>DataTypes</SHORT-NAME>
      <ELEMENTS>
        <IMPLEMENTATION-DATA-TYPE UUID="11111111-0000-0000-0000-000000000001">
          <SHORT-NAME>uint16</SHORT-NAME>
          <CATEGORY>VALUE</CATEGORY>
          <SW-DATA-DEF-PROPS>
            <SW-DATA-DEF-PROPS-VARIANTS>
              <SW-DATA-DEF-PROPS-CONDITIONAL>
                <BASE-TYPE-REF DEST="SW-BASE-TYPE">/DataTypes/BaseTypes/uint16</BASE-TYPE-REF>
              </SW-DATA-DEF-PROPS-CONDITIONAL>
            </SW-DATA-DEF-PROPS-VARIANTS>
          </SW-DATA-DEF-PROPS>
        </IMPLEMENTATION-DATA-TYPE>
        <IMPLEMENTATION-DATA-TYPE>
          <SHORT-NAME>boolean</SHORT-NAME>
          <CATEGORY>VALUE</CATEGORY>
          <SW-DATA-DEF-PROPS>
            <SW-DATA-DEF-PROPS-VARIANTS>
              <SW-DATA-DEF-PROPS-CONDITIONAL>
                <BASE-TYPE-REF DEST="SW-BASE-TYPE">/DataTypes/BaseTypes/boolean</BASE-TYPE-REF>
              </SW-DATA-DEF-PROPS-CONDITIONAL>
            </SW-DATA-DEF-PROPS-VARIANTS>
          </SW-DATA-DEF-PROPS>
        </IMPLEMENTATION-DATA-TYPE>
        <IMPLEMENTATION-DATA-TYPE>
          <SHORT-NAME>Gear_T</SHORT-NAME>
          <CATEGORY>VALUE</CATEGORY>
          <SW-DATA-DEF-PROPS>
            <SW-DATA-DEF-PROPS-VARIANTS>
              <SW-DATA-DEF-PROPS-CONDITIONAL>
                <BASE-TYPE-REF DEST="SW-BASE-TYPE">/DataTypes/BaseTypes/uint16</BASE-TYPE-REF>
                <COMPU-METHOD-REF DEST="COMPU-METHOD">/DataTypes/CompuMethods/CM_Gear</COMPU-METHOD-REF>
              </SW-DATA-DEF-PROPS-CONDITIONAL>
            </SW-DATA-DEF-PROPS-VARIANTS>
          </SW-DATA-DEF-PROPS>
        </IMPLEMENTATION-DATA-TYPE>
        <IMPLEMENTATION-DATA-TYPE>
          <SHORT-NAME>Speed_Alias</SHORT-NAME>
          <CATEGORY>TYPE_REFERENCE</CATEGORY>
          <SW-DATA-DEF-PROPS>
            <SW-DATA-DEF-PROPS-VARIANTS>
              <SW-DATA-DEF-PROPS-CONDITIONAL>
                <IMPLEMENTATION-DATA-TYPE-REF DEST="IMPLEMENTATION-DATA-TYPE">/DataTypes/uint16</IMPLEMENTATION-DATA-TYPE-REF>
              </SW-DATA-DEF-PROPS-CONDITIONAL>
            </SW-DATA-DEF-PROPS-VARIANTS>
          </SW-DATA-DEF-PROPS>
        </IMPLEMENTATION-DATA-TYPE>
        <IMPLEMENTATION-DATA-TYPE>
          <SHORT-NAME>Arr8</SHORT-NAME>
          <CATEGORY>ARRAY</CATEGORY>
          <SUB-ELEMENTS>
            <IMPLEMENTATION-DATA-TYPE-ELEMENT>
              <SHORT-NAME>Element</SHORT-NAME>
              <CATEGORY>TYPE_REFERENCE</CATEGORY>
              <ARRAY-SIZE>8</ARRAY-SIZE>
              <ARRAY-SIZE-SEMANTICS>FIXED-SIZE</ARRAY-SIZE-SEMANTICS>
              <SW-DATA-DEF-PROPS>
                <SW-DATA-DEF-PROPS-VARIANTS>
                  <SW-DATA-DEF-PROPS-CONDITIONAL>
                    <IMPLEMENTATION-DATA-TYPE-REF DEST="IMPLEMENTATION-DATA-TYPE">/DataTypes/uint16</IMPLEMENTATION-DATA-TYPE-REF>
                  </SW-DATA-DEF-PROPS-CONDITIONAL>
                </SW-DATA-DEF-PROPS-VARIANTS>
              </SW-DATA-DEF-PROPS>
            </IMPLEMENTATION-DATA-TYPE-ELEMENT>
          </SUB-ELEMENTS>
        </IMPLEMENTATION-DATA-TYPE>
        <IMPLEMENTATION-DATA-TYPE>
          <SHORT-NAME>Rec</SHORT-NAME>
          <CATEGORY>STRUCTURE</CATEGORY>
          <SUB-ELEMENTS>
            <IMPLEMENTATION-DATA-TYPE-ELEMENT>
              <SHORT-NAME>speed</SHORT-NAME>
              <CATEGORY>TYPE_REFERENCE</CATEGORY>
              <SW-DATA-DEF-PROPS>
                <SW-DATA-DEF-PROPS-VARIANTS>
                  <SW-DATA-DEF-PROPS-CONDITIONAL>
                    <IMPLEMENTATION-DATA-TYPE-REF DEST="IMPLEMENTATION-DATA-TYPE">/DataTypes/uint16</IMPLEMENTATION-DATA-TYPE-REF>
                  </SW-DATA-DEF-PROPS-CONDITIONAL>
                </SW-DATA-DEF-PROPS-VARIANTS>
              </SW-DATA-DEF-PROPS>
            </IMPLEMENTATION-DATA-TYPE-ELEMENT>
            <IMPLEMENTATION-DATA-TYPE-ELEMENT>
              <SHORT-NAME>valid</SHORT-NAME>
              <CATEGORY>TYPE_REFERENCE</CATEGORY>
              <SW-DATA-DEF-PROPS>
                <SW-DATA-DEF-PROPS-VARIANTS>
                  <SW-DATA-DEF-PROPS-CONDITIONAL>
                    <IMPLEMENTATION-DATA-TYPE-REF DEST="IMPLEMENTATION-DATA-TYPE">/DataTypes/boolean</IMPLEMENTATION-DATA-TYPE-REF>
                  </SW-DATA-DEF-PROPS-CONDITIONAL>
                </SW-DATA-DEF-PROPS-VARIANTS>
              </SW-DATA-DEF-PROPS>
            </IMPLEMENTATION-DATA-TYPE-ELEMENT>
          </SUB-ELEMENTS>
        </IMPLEMENTATION-DATA-TYPE>
        <APPLICATION-PRIMITIVE-DATA-TYPE>
          <SHORT-NAME>AppSpeed</SHORT-NAME>
          <CATEGORY>VALUE</CATEGORY>
          <SW-DATA-DEF-PROPS>
            <SW-DATA-DEF-PROPS-VARIANTS>
              <SW-DATA-DEF-PROPS-CONDITIONAL>
                <COMPU-METHOD-REF DEST="COMPU-METHOD">/DataTypes/CompuMethods/CM_Speed</COMPU-METHOD-REF>
                <DATA-CONSTR-REF DEST="DATA-CONSTR">/DataTypes/DataConstrs/DC_Speed</DATA-CONSTR-REF>
                <UNIT-REF DEST="UNIT">/DataTypes/Units/KmPerHour</UNIT-REF>
              </SW-DATA-DEF-PROPS-CONDITIONAL>
            </SW-DATA-DEF-PROPS-VARIANTS>
          </SW-DATA-DEF-PROPS>
        </APPLICATION-PRIMITIVE-DATA-TYPE>
        <APPLICATION-ARRAY-DATA-TYPE>
          <SHORT-NAME>AppSpeedArray</SHORT-NAME>
          <CATEGORY>ARRAY</CATEGORY>
          <ELEMENT>
            <SHORT-NAME>Element</SHORT-NAME>
            <CATEGORY>VALUE</CATEGORY>
            <TYPE-TREF DEST="APPLICATION-PRIMITIVE-DATA-TYPE">/DataTypes/AppSpeed</TYPE-TREF>
            <ARRAY-SIZE-SEMANTICS>FIXED-SIZE</ARRAY-SIZE-SEMANTICS>
            <MAX-NUMBER-OF-ELEMENTS>4</MAX-NUMBER-OF-ELEMENTS>
          </ELEMENT>
        </APPLICATION-ARRAY-DATA-TYPE>
        <APPLICATION-RECORD-DATA-TYPE>
          <SHORT-NAME>AppRec</SHORT-NAME>
          <CATEGORY>STRUCTURE</CATEGORY>
          <ELEMENTS>
            <APPLICATION-RECORD-ELEMENT>
              <SHORT-NAME>front</SHORT-NAME>
              <TYPE-TREF DEST="APPLICATION-PRIMITIVE-DATA-TYPE">/DataTypes/AppSpeed</TYPE-TREF>
            </APPLICATION-RECORD-ELEMENT>
            <APPLICATION-RECORD-ELEMENT>
              <SHORT-NAME>rear</SHORT-NAME>
              <TYPE-TREF DEST="APPLICATION-PRIMITIVE-DATA-TYPE">/DataTypes/AppSpeed</TYPE-TREF>
            </APPLICATION-RECORD-ELEMENT>
          </ELEMENTS>
        </APPLICATION-RECORD-DATA-TYPE>
        <DATA-TYPE-MAPPING-SET>
          <SHORT-NAME>Mappings</SHORT-NAME>
          <DATA-TYPE-MAPS>
            <DATA-TYPE-MAP>
              <APPLICATION-DATA-TYPE-REF DEST="APPLICATION-PRIMITIVE-DATA-TYPE">/DataTypes/AppSpeed</APPLICATION-DATA-TYPE-REF>
              <IMPLEMENTATION-DATA-TYPE-REF DEST="IMPLEMENTATION-DATA-TYPE">/DataTypes/uint16</IMPLEMENTATION-DATA-TYPE-REF>
            </DATA-TYPE-MAP>
          </DATA-TYPE-MAPS>
        </DATA-TYPE-MAPPING-SET>
      </ELEMENTS>
      <AR-PACKAGES>
        <AR-PACKAGE>
          <SHORT-NAME>BaseTypes</SHORT-NAME>
          <ELEMENTS>
            <SW-BASE-TYPE UUID="11111111-0000-0000-0000-0000000000b1">
              <SHORT-NAME>uint16</SHORT-NAME>
              <CATEGORY>FIXED_LENGTH</CATEGORY>
              <BASE-TYPE-SIZE>16</BASE-TYPE-SIZE>
              <BASE-TYPE-ENCODING>NONE</BASE-TYPE-ENCODING>
              <NATIVE-DECLARATION>unsigned short</NATIVE-DECLARATION>
            </SW-BASE-TYPE>
            <SW-BASE-TYPE>
              <SHORT-NAME>boolean</SHORT-NAME>
              <CATEGORY>FIXED_LENGTH</CATEGORY>
              <BASE-TYPE-SIZE>8</BASE-TYPE-SIZE>
              <BASE-TYPE-ENCODING>BOOLEAN</BASE-TYPE-ENCODING>
            </SW-BASE-TYPE>
          </ELEMENTS>
        </AR-PACKAGE>
        <AR-PACKAGE>
          <SHORT-NAME>CompuMethods</SHORT-NAME>
          <ELEMENTS>
            <COMPU-METHOD>
              <SHORT-NAME>CM_Speed</SHORT-NAME>
              <CATEGORY>LINEAR</CATEGORY>
              <UNIT-REF DEST="UNIT">/DataTypes/Units/KmPerHour</UNIT-REF>
              <COMPU-INTERNAL-TO-PHYS>
                <COMPU-SCALES>
                  <COMPU-SCALE>
                    <COMPU-RATIONAL-COEFFS>
                      <COMPU-NUMERATOR><V>-40</V><V>0.01</V></COMPU-NUMERATOR>
                      <COMPU-DENOMINATOR><V>1</V></COMPU-DENOMINATOR>
                    </COMPU-RATIONAL-COEFFS>
                  </COMPU-SCALE>
                </COMPU-SCALES>
              </COMPU-INTERNAL-TO-PHYS>
            </COMPU-METHOD>
            <COMPU-METHOD>
              <SHORT-NAME>CM_Gear</SHORT-NAME>
              <CATEGORY>TEXTTABLE</CATEGORY>
              <COMPU-INTERNAL-TO-PHYS>
                <COMPU-SCALES>
                  <COMPU-SCALE>
                    <LOWER-LIMIT INTERVAL-TYPE="CLOSED">0</LOWER-LIMIT>
                    <UPPER-LIMIT INTERVAL-TYPE="CLOSED">0</UPPER-LIMIT>
                    <COMPU-CONST><VT>PARK</VT></COMPU-CONST>
                  </COMPU-SCALE>
                  <COMPU-SCALE>
                    <LOWER-LIMIT INTERVAL-TYPE="CLOSED">1</LOWER-LIMIT>
                    <UPPER-LIMIT INTERVAL-TYPE="CLOSED">1</UPPER-LIMIT>
                    <COMPU-CONST><VT>REVERSE</VT></COMPU-CONST>
                  </COMPU-SCALE>
                  <COMPU-SCALE>
                    <LOWER-LIMIT INTERVAL-TYPE="CLOSED">0x2</LOWER-LIMIT>
                    <UPPER-LIMIT INTERVAL-TYPE="CLOSED">0x2</UPPER-LIMIT>
                    <COMPU-CONST><VT>DRIVE</VT></COMPU-CONST>
                  </COMPU-SCALE>
                </COMPU-SCALES>
              </COMPU-INTERNAL-TO-PHYS>
            </COMPU-METHOD>
          </ELEMENTS>
        </AR-PACKAGE>
        <AR-PACKAGE>
          <SHORT-NAME>DataConstrs</SHORT-NAME>
          <ELEMENTS>
            <DATA-CONSTR>
              <SHORT-NAME>DC_Speed</SHORT-NAME>
              <DATA-CONSTR-RULES>
                <DATA-CONSTR-RULE>
                  <PHYS-CONSTRS>
                    <LOWER-LIMIT INTERVAL-TYPE="CLOSED">-40</LOWER-LIMIT>
                    <UPPER-LIMIT INTERVAL-TYPE="CLOSED">615.35</UPPER-LIMIT>
                  </PHYS-CONSTRS>
                </DATA-CONSTR-RULE>
              </DATA-CONSTR-RULES>
            </DATA-CONSTR>
          </ELEMENTS>
        </AR-PACKAGE>
        <AR-PACKAGE>
          <SHORT-NAME>Units</SHORT-NAME>
          <ELEMENTS>
            <UNIT>
              <SHORT-NAME>KmPerHour</SHORT-NAME>
              <DISPLAY-NAME>km/h</DISPLAY-NAME>
            </UNIT>
          </ELEMENTS>
        </AR-PACKAGE>
      </AR-PACKAGES>
    </AR-PACKAGE>
    <AR-PACKAGE>
      <SHORT-NAME>Constants</SHORT-NAME>
      <ELEMENTS>
        <CONSTANT-SPECIFICATION>
          <SHORT-NAME>C_TorqueInit</SHORT-NAME>
          <VALUE-SPEC>
            <NUMERICAL-VALUE-SPECIFICATION>
              <VALUE>7</VALUE>
            </NUMERICAL-VALUE-SPECIFICATION>
          </VALUE-SPEC>
        </CONSTANT-SPECIFICATION>
        <CONSTANT-SPECIFICATION>
          <SHORT-NAME>C_Table</SHORT-NAME>
          <VALUE-SPEC>
            <ARRAY-VALUE-SPECIFICATION>
              <ELEMENTS>
                <NUMERICAL-VALUE-SPECIFICATION><VALUE>1</VALUE></NUMERICAL-VALUE-SPECIFICATION>
                <NUMERICAL-VALUE-SPECIFICATION><VALUE>2</VALUE></NUMERICAL-VALUE-SPECIFICATION>
                <RECORD-VALUE-SPECIFICATION>
                  <FIELDS>
                    <NUMERICAL-VALUE-SPECIFICATION><VALUE>3</VALUE></NUMERICAL-VALUE-SPECIFICATION>
                    <TEXT-VALUE-SPECIFICATION><VALUE>four</VALUE></TEXT-VALUE-SPECIFICATION>
                  </FIELDS>
                </RECORD-VALUE-SPECIFICATION>
              </ELEMENTS>
            </ARRAY-VALUE-SPECIFICATION>
          </VALUE-SPEC>
        </CONSTANT-SPECIFICATION>
      </ELEMENTS>
    </AR-PACKAGE>
  </AR-PACKAGES>
</AUTOSAR>
"""

INTERFACES = f"""<?xml version="1.0" encoding="UTF-8"?>
<AUTOSAR xmlns="{NS}" xmlns:xsi="{XSI}" xsi:schemaLocation="{NS} AUTOSAR_4-2-2.xsd">
  <AR-PACKAGES>
    <AR-PACKAGE>
      <SHORT-NAME>PortInterfaces</SHORT-NAME>
      <ELEMENTS>
        <SENDER-RECEIVER-INTERFACE UUID="22222222-0000-0000-0000-000000000001">
          <SHORT-NAME>IfSpeed</SHORT-NAME>
          <IS-SERVICE>false</IS-SERVICE>
          <DATA-ELEMENTS>
            <VARIABLE-DATA-PROTOTYPE>
              <SHORT-NAME>Speed</SHORT-NAME>
              <SW-DATA-DEF-PROPS>
                <SW-DATA-DEF-PROPS-VARIANTS>
                  <SW-DATA-DEF-PROPS-CONDITIONAL>
                    <SW-IMPL-POLICY>STANDARD</SW-IMPL-POLICY>
                  </SW-DATA-DEF-PROPS-CONDITIONAL>
                </SW-DATA-DEF-PROPS-VARIANTS>
              </SW-DATA-DEF-PROPS>
              <TYPE-TREF DEST="IMPLEMENTATION-DATA-TYPE">/DataTypes/uint16</TYPE-TREF>
            </VARIABLE-DATA-PROTOTYPE>
          </DATA-ELEMENTS>
        </SENDER-RECEIVER-INTERFACE>
        <SENDER-RECEIVER-INTERFACE>
          <SHORT-NAME>IfTorque</SHORT-NAME>
          <IS-SERVICE>false</IS-SERVICE>
          <DATA-ELEMENTS>
            <VARIABLE-DATA-PROTOTYPE>
              <SHORT-NAME>Torque</SHORT-NAME>
              <TYPE-TREF DEST="IMPLEMENTATION-DATA-TYPE">/DataTypes/uint16</TYPE-TREF>
            </VARIABLE-DATA-PROTOTYPE>
          </DATA-ELEMENTS>
        </SENDER-RECEIVER-INTERFACE>
        <SENDER-RECEIVER-INTERFACE>
          <SHORT-NAME>IfEvents</SHORT-NAME>
          <IS-SERVICE>false</IS-SERVICE>
          <DATA-ELEMENTS>
            <VARIABLE-DATA-PROTOTYPE>
              <SHORT-NAME>Event</SHORT-NAME>
              <SW-DATA-DEF-PROPS>
                <SW-DATA-DEF-PROPS-VARIANTS>
                  <SW-DATA-DEF-PROPS-CONDITIONAL>
                    <SW-IMPL-POLICY>QUEUED</SW-IMPL-POLICY>
                  </SW-DATA-DEF-PROPS-CONDITIONAL>
                </SW-DATA-DEF-PROPS-VARIANTS>
              </SW-DATA-DEF-PROPS>
              <TYPE-TREF DEST="IMPLEMENTATION-DATA-TYPE">/DataTypes/uint16</TYPE-TREF>
            </VARIABLE-DATA-PROTOTYPE>
          </DATA-ELEMENTS>
        </SENDER-RECEIVER-INTERFACE>
        <CLIENT-SERVER-INTERFACE>
          <SHORT-NAME>IfDiag</SHORT-NAME>
          <IS-SERVICE>true</IS-SERVICE>
          <OPERATIONS>
            <CLIENT-SERVER-OPERATION>
              <SHORT-NAME>ReadDtc</SHORT-NAME>
              <ARGUMENTS>
                <ARGUMENT-DATA-PROTOTYPE>
                  <SHORT-NAME>Index</SHORT-NAME>
                  <TYPE-TREF DEST="IMPLEMENTATION-DATA-TYPE">/DataTypes/uint16</TYPE-TREF>
                  <DIRECTION>IN</DIRECTION>
                </ARGUMENT-DATA-PROTOTYPE>
                <ARGUMENT-DATA-PROTOTYPE>
                  <SHORT-NAME>Dtc</SHORT-NAME>
                  <TYPE-TREF DEST="IMPLEMENTATION-DATA-TYPE">/DataTypes/uint16</TYPE-TREF>
                  <DIRECTION>OUT</DIRECTION>
                </ARGUMENT-DATA-PROTOTYPE>
              </ARGUMENTS>
              <POSSIBLE-ERROR-REFS>
                <POSSIBLE-ERROR-REF DEST="APPLICATION-ERROR">/PortInterfaces/IfDiag/E_NOT_OK</POSSIBLE-ERROR-REF>
              </POSSIBLE-ERROR-REFS>
            </CLIENT-SERVER-OPERATION>
            <CLIENT-SERVER-OPERATION>
              <SHORT-NAME>Clear</SHORT-NAME>
            </CLIENT-SERVER-OPERATION>
          </OPERATIONS>
          <POSSIBLE-ERRORS>
            <APPLICATION-ERROR>
              <SHORT-NAME>E_NOT_OK</SHORT-NAME>
              <ERROR-CODE>1</ERROR-CODE>
            </APPLICATION-ERROR>
          </POSSIBLE-ERRORS>
        </CLIENT-SERVER-INTERFACE>
        <MODE-SWITCH-INTERFACE>
          <SHORT-NAME>IfMode</SHORT-NAME>
          <IS-SERVICE>false</IS-SERVICE>
          <MODE-GROUP>
            <SHORT-NAME>currentMode</SHORT-NAME>
            <TYPE-TREF DEST="MODE-DECLARATION-GROUP">/Modes/MdgEcu</TYPE-TREF>
          </MODE-GROUP>
        </MODE-SWITCH-INTERFACE>
        <PARAMETER-INTERFACE>
          <SHORT-NAME>IfCal</SHORT-NAME>
          <PARAMETERS>
            <PARAMETER-DATA-PROTOTYPE>
              <SHORT-NAME>Gain</SHORT-NAME>
              <TYPE-TREF DEST="IMPLEMENTATION-DATA-TYPE">/DataTypes/uint16</TYPE-TREF>
              <INIT-VALUE>
                <NUMERICAL-VALUE-SPECIFICATION><VALUE>100</VALUE></NUMERICAL-VALUE-SPECIFICATION>
              </INIT-VALUE>
            </PARAMETER-DATA-PROTOTYPE>
          </PARAMETERS>
        </PARAMETER-INTERFACE>
        <NV-DATA-INTERFACE>
          <SHORT-NAME>IfNv</SHORT-NAME>
          <NV-DATAS>
            <VARIABLE-DATA-PROTOTYPE>
              <SHORT-NAME>Stored</SHORT-NAME>
              <TYPE-TREF DEST="IMPLEMENTATION-DATA-TYPE">/DataTypes/uint16</TYPE-TREF>
            </VARIABLE-DATA-PROTOTYPE>
          </NV-DATAS>
        </NV-DATA-INTERFACE>
        <TRIGGER-INTERFACE>
          <SHORT-NAME>IfTrig</SHORT-NAME>
          <TRIGGERS>
            <TRIGGER>
              <SHORT-NAME>Tick</SHORT-NAME>
            </TRIGGER>
          </TRIGGERS>
        </TRIGGER-INTERFACE>
      </ELEMENTS>
    </AR-PACKAGE>
    <AR-PACKAGE>
      <SHORT-NAME>Modes</SHORT-NAME>
      <ELEMENTS>
        <MODE-DECLARATION-GROUP>
          <SHORT-NAME>MdgEcu</SHORT-NAME>
          <INITIAL-MODE-REF DEST="MODE-DECLARATION">/Modes/MdgEcu/STARTUP</INITIAL-MODE-REF>
          <MODE-DECLARATIONS>
            <MODE-DECLARATION><SHORT-NAME>STARTUP</SHORT-NAME></MODE-DECLARATION>
            <MODE-DECLARATION><SHORT-NAME>RUN</SHORT-NAME></MODE-DECLARATION>
            <MODE-DECLARATION><SHORT-NAME>SHUTDOWN</SHORT-NAME></MODE-DECLARATION>
          </MODE-DECLARATIONS>
        </MODE-DECLARATION-GROUP>
      </ELEMENTS>
    </AR-PACKAGE>
  </AR-PACKAGES>
</AUTOSAR>
"""

SWC_A = f"""<?xml version="1.0" encoding="UTF-8"?>
<AUTOSAR xmlns="{NS}" xmlns:xsi="{XSI}" xsi:schemaLocation="{NS} AUTOSAR_4-2-2.xsd">
  <AR-PACKAGES>
    <AR-PACKAGE>
      <SHORT-NAME>ComponentTypes</SHORT-NAME>
      <ELEMENTS>
        <APPLICATION-SW-COMPONENT-TYPE UUID="aaaaaaaa-0000-0000-0000-000000000001">
          <SHORT-NAME>SwcA</SHORT-NAME>
          <PORTS>
            <P-PORT-PROTOTYPE UUID="aaaaaaaa-0000-0000-0000-000000000011">
              <SHORT-NAME>PpSpeed</SHORT-NAME>
              <PROVIDED-COM-SPECS>
                <NONQUEUED-SENDER-COM-SPEC>
                  <DATA-ELEMENT-REF DEST="VARIABLE-DATA-PROTOTYPE">/PortInterfaces/IfSpeed/Speed</DATA-ELEMENT-REF>
                  <INIT-VALUE>
                    <NUMERICAL-VALUE-SPECIFICATION><VALUE>0</VALUE></NUMERICAL-VALUE-SPECIFICATION>
                  </INIT-VALUE>
                </NONQUEUED-SENDER-COM-SPEC>
              </PROVIDED-COM-SPECS>
              <PROVIDED-INTERFACE-TREF DEST="SENDER-RECEIVER-INTERFACE">/PortInterfaces/IfSpeed</PROVIDED-INTERFACE-TREF>
            </P-PORT-PROTOTYPE>
            <R-PORT-PROTOTYPE>
              <SHORT-NAME>RpTorque</SHORT-NAME>
              <REQUIRED-COM-SPECS>
                <NONQUEUED-RECEIVER-COM-SPEC>
                  <DATA-ELEMENT-REF DEST="VARIABLE-DATA-PROTOTYPE">/PortInterfaces/IfTorque/Torque</DATA-ELEMENT-REF>
                  <ALIVE-TIMEOUT>0</ALIVE-TIMEOUT>
                  <INIT-VALUE>
                    <CONSTANT-REFERENCE>
                      <CONSTANT-REF DEST="CONSTANT-SPECIFICATION">/Constants/C_TorqueInit</CONSTANT-REF>
                    </CONSTANT-REFERENCE>
                  </INIT-VALUE>
                </NONQUEUED-RECEIVER-COM-SPEC>
              </REQUIRED-COM-SPECS>
              <REQUIRED-INTERFACE-TREF DEST="SENDER-RECEIVER-INTERFACE">/PortInterfaces/IfTorque</REQUIRED-INTERFACE-TREF>
            </R-PORT-PROTOTYPE>
            <R-PORT-PROTOTYPE>
              <SHORT-NAME>RpDiag</SHORT-NAME>
              <REQUIRED-INTERFACE-TREF DEST="CLIENT-SERVER-INTERFACE">/PortInterfaces/IfDiag</REQUIRED-INTERFACE-TREF>
            </R-PORT-PROTOTYPE>
            <P-PORT-PROTOTYPE>
              <SHORT-NAME>PpEvents</SHORT-NAME>
              <PROVIDED-COM-SPECS>
                <QUEUED-SENDER-COM-SPEC>
                  <DATA-ELEMENT-REF DEST="VARIABLE-DATA-PROTOTYPE">/PortInterfaces/IfEvents/Event</DATA-ELEMENT-REF>
                </QUEUED-SENDER-COM-SPEC>
              </PROVIDED-COM-SPECS>
              <PROVIDED-INTERFACE-TREF DEST="SENDER-RECEIVER-INTERFACE">/PortInterfaces/IfEvents</PROVIDED-INTERFACE-TREF>
            </P-PORT-PROTOTYPE>
            <R-PORT-PROTOTYPE>
              <SHORT-NAME>RpMode</SHORT-NAME>
              <REQUIRED-COM-SPECS>
                <MODE-SWITCH-RECEIVER-COM-SPEC>
                  <MODE-GROUP-REF DEST="MODE-DECLARATION-GROUP-PROTOTYPE">/PortInterfaces/IfMode/currentMode</MODE-GROUP-REF>
                </MODE-SWITCH-RECEIVER-COM-SPEC>
              </REQUIRED-COM-SPECS>
              <REQUIRED-INTERFACE-TREF DEST="MODE-SWITCH-INTERFACE">/PortInterfaces/IfMode</REQUIRED-INTERFACE-TREF>
            </R-PORT-PROTOTYPE>
            <PR-PORT-PROTOTYPE>
              <SHORT-NAME>PrTorque</SHORT-NAME>
              <PROVIDED-COM-SPECS>
                <NONQUEUED-SENDER-COM-SPEC>
                  <DATA-ELEMENT-REF DEST="VARIABLE-DATA-PROTOTYPE">/PortInterfaces/IfTorque/Torque</DATA-ELEMENT-REF>
                  <INIT-VALUE>
                    <NUMERICAL-VALUE-SPECIFICATION><VALUE>1</VALUE></NUMERICAL-VALUE-SPECIFICATION>
                  </INIT-VALUE>
                </NONQUEUED-SENDER-COM-SPEC>
              </PROVIDED-COM-SPECS>
              <REQUIRED-COM-SPECS>
                <NONQUEUED-RECEIVER-COM-SPEC>
                  <DATA-ELEMENT-REF DEST="VARIABLE-DATA-PROTOTYPE">/PortInterfaces/IfTorque/Torque</DATA-ELEMENT-REF>
                  <INIT-VALUE>
                    <NUMERICAL-VALUE-SPECIFICATION><VALUE>2</VALUE></NUMERICAL-VALUE-SPECIFICATION>
                  </INIT-VALUE>
                </NONQUEUED-RECEIVER-COM-SPEC>
              </REQUIRED-COM-SPECS>
              <PROVIDED-REQUIRED-INTERFACE-TREF DEST="SENDER-RECEIVER-INTERFACE">/PortInterfaces/IfTorque</PROVIDED-REQUIRED-INTERFACE-TREF>
            </PR-PORT-PROTOTYPE>
          </PORTS>
          <INTERNAL-BEHAVIORS>
            <SWC-INTERNAL-BEHAVIOR UUID="aaaaaaaa-0000-0000-0000-0000000000b0">
              <SHORT-NAME>SwcA_InternalBehavior</SHORT-NAME>
              <DATA-TYPE-MAPPING-REFS>
                <DATA-TYPE-MAPPING-REF DEST="DATA-TYPE-MAPPING-SET">/DataTypes/Mappings</DATA-TYPE-MAPPING-REF>
              </DATA-TYPE-MAPPING-REFS>
              <EVENTS>
                <TIMING-EVENT UUID="aaaaaaaa-0000-0000-0000-0000000000e1">
                  <SHORT-NAME>TMT_RunA_10ms</SHORT-NAME>
                  <START-ON-EVENT-REF DEST="RUNNABLE-ENTITY">/ComponentTypes/SwcA/SwcA_InternalBehavior/RunA_10ms</START-ON-EVENT-REF>
                  <PERIOD>0.01</PERIOD>
                </TIMING-EVENT>
                <INIT-EVENT>
                  <SHORT-NAME>IT_RunA_Init</SHORT-NAME>
                  <START-ON-EVENT-REF DEST="RUNNABLE-ENTITY">/ComponentTypes/SwcA/SwcA_InternalBehavior/RunA_Init</START-ON-EVENT-REF>
                </INIT-EVENT>
                <SWC-MODE-SWITCH-EVENT>
                  <SHORT-NAME>MST_RunA_OnRun</SHORT-NAME>
                  <START-ON-EVENT-REF DEST="RUNNABLE-ENTITY">/ComponentTypes/SwcA/SwcA_InternalBehavior/RunA_Init</START-ON-EVENT-REF>
                  <ACTIVATION>ON-ENTRY</ACTIVATION>
                  <MODE-IREFS>
                    <MODE-IREF>
                      <CONTEXT-PORT-REF DEST="R-PORT-PROTOTYPE">/ComponentTypes/SwcA/RpMode</CONTEXT-PORT-REF>
                      <CONTEXT-MODE-DECLARATION-GROUP-PROTOTYPE-REF DEST="MODE-DECLARATION-GROUP-PROTOTYPE">/PortInterfaces/IfMode/currentMode</CONTEXT-MODE-DECLARATION-GROUP-PROTOTYPE-REF>
                      <TARGET-MODE-DECLARATION-REF DEST="MODE-DECLARATION">/Modes/MdgEcu/RUN</TARGET-MODE-DECLARATION-REF>
                    </MODE-IREF>
                  </MODE-IREFS>
                </SWC-MODE-SWITCH-EVENT>
                <DATA-SEND-COMPLETED-EVENT>
                  <SHORT-NAME>DSC_RunA_Sent</SHORT-NAME>
                  <START-ON-EVENT-REF DEST="RUNNABLE-ENTITY">/ComponentTypes/SwcA/SwcA_InternalBehavior/RunA_Init</START-ON-EVENT-REF>
                  <EVENT-SOURCE-REF DEST="VARIABLE-ACCESS">/ComponentTypes/SwcA/SwcA_InternalBehavior/RunA_10ms/DSP_PpEvents_Event</EVENT-SOURCE-REF>
                </DATA-SEND-COMPLETED-EVENT>
              </EVENTS>
              <RUNNABLES>
                <RUNNABLE-ENTITY UUID="aaaaaaaa-0000-0000-0000-0000000000f1">
                  <SHORT-NAME>RunA_10ms</SHORT-NAME>
                  <MINIMUM-START-INTERVAL>0.005</MINIMUM-START-INTERVAL>
                  <CAN-BE-INVOKED-CONCURRENTLY>false</CAN-BE-INVOKED-CONCURRENTLY>
                  <DATA-SEND-POINTS>
                    <VARIABLE-ACCESS>
                      <SHORT-NAME>DSP_PpEvents_Event</SHORT-NAME>
                      <ACCESSED-VARIABLE>
                        <AUTOSAR-VARIABLE-IREF>
                          <PORT-PROTOTYPE-REF DEST="P-PORT-PROTOTYPE">/ComponentTypes/SwcA/PpEvents</PORT-PROTOTYPE-REF>
                          <TARGET-DATA-PROTOTYPE-REF DEST="VARIABLE-DATA-PROTOTYPE">/PortInterfaces/IfEvents/Event</TARGET-DATA-PROTOTYPE-REF>
                        </AUTOSAR-VARIABLE-IREF>
                      </ACCESSED-VARIABLE>
                    </VARIABLE-ACCESS>
                  </DATA-SEND-POINTS>
                  <DATA-WRITE-ACCESSS>
                    <VARIABLE-ACCESS UUID="aaaaaaaa-0000-0000-0000-0000000000a1">
                      <SHORT-NAME>DWA_PpSpeed_Speed</SHORT-NAME>
                      <ACCESSED-VARIABLE>
                        <AUTOSAR-VARIABLE-IREF>
                          <PORT-PROTOTYPE-REF DEST="P-PORT-PROTOTYPE">/ComponentTypes/SwcA/PpSpeed</PORT-PROTOTYPE-REF>
                          <TARGET-DATA-PROTOTYPE-REF DEST="VARIABLE-DATA-PROTOTYPE">/PortInterfaces/IfSpeed/Speed</TARGET-DATA-PROTOTYPE-REF>
                        </AUTOSAR-VARIABLE-IREF>
                      </ACCESSED-VARIABLE>
                    </VARIABLE-ACCESS>
                  </DATA-WRITE-ACCESSS>
                  <MODE-ACCESS-POINTS>
                    <MODE-ACCESS-POINT>
                      <MODE-GROUP-IREF>
                        <R-MODE-GROUP-IN-ATOMIC-SWC-INSTANCE-REF>
                          <CONTEXT-R-PORT-REF DEST="R-PORT-PROTOTYPE">/ComponentTypes/SwcA/RpMode</CONTEXT-R-PORT-REF>
                          <TARGET-MODE-GROUP-REF DEST="MODE-DECLARATION-GROUP-PROTOTYPE">/PortInterfaces/IfMode/currentMode</TARGET-MODE-GROUP-REF>
                        </R-MODE-GROUP-IN-ATOMIC-SWC-INSTANCE-REF>
                      </MODE-GROUP-IREF>
                    </MODE-ACCESS-POINT>
                  </MODE-ACCESS-POINTS>
                  <SERVER-CALL-POINTS>
                    <SYNCHRONOUS-SERVER-CALL-POINT>
                      <SHORT-NAME>SSCP_RpDiag_ReadDtc</SHORT-NAME>
                      <OPERATION-IREF>
                        <CONTEXT-R-PORT-REF DEST="R-PORT-PROTOTYPE">/ComponentTypes/SwcA/RpDiag</CONTEXT-R-PORT-REF>
                        <TARGET-REQUIRED-OPERATION-REF DEST="CLIENT-SERVER-OPERATION">/PortInterfaces/IfDiag/ReadDtc</TARGET-REQUIRED-OPERATION-REF>
                      </OPERATION-IREF>
                      <TIMEOUT>0</TIMEOUT>
                    </SYNCHRONOUS-SERVER-CALL-POINT>
                    <ASYNCHRONOUS-SERVER-CALL-POINT>
                      <SHORT-NAME>ASCP_RpDiag_Clear</SHORT-NAME>
                      <OPERATION-IREF>
                        <CONTEXT-R-PORT-REF DEST="R-PORT-PROTOTYPE">/ComponentTypes/SwcA/RpDiag</CONTEXT-R-PORT-REF>
                        <TARGET-REQUIRED-OPERATION-REF DEST="CLIENT-SERVER-OPERATION">/PortInterfaces/IfDiag/Clear</TARGET-REQUIRED-OPERATION-REF>
                      </OPERATION-IREF>
                    </ASYNCHRONOUS-SERVER-CALL-POINT>
                  </SERVER-CALL-POINTS>
                  <SYMBOL>RunA_10ms_Symbol</SYMBOL>
                </RUNNABLE-ENTITY>
                <RUNNABLE-ENTITY>
                  <SHORT-NAME>RunA_Init</SHORT-NAME>
                  <CAN-BE-INVOKED-CONCURRENTLY>true</CAN-BE-INVOKED-CONCURRENTLY>
                  <SYMBOL>RunA_Init</SYMBOL>
                </RUNNABLE-ENTITY>
              </RUNNABLES>
            </SWC-INTERNAL-BEHAVIOR>
          </INTERNAL-BEHAVIORS>
        </APPLICATION-SW-COMPONENT-TYPE>
      </ELEMENTS>
    </AR-PACKAGE>
  </AR-PACKAGES>
</AUTOSAR>
"""

SWC_B = f"""<?xml version="1.0" encoding="UTF-8"?>
<AUTOSAR xmlns="{NS}" xmlns:xsi="{XSI}" xsi:schemaLocation="{NS} AUTOSAR_4-2-2.xsd">
  <AR-PACKAGES>
    <AR-PACKAGE>
      <SHORT-NAME>ComponentTypes</SHORT-NAME>
      <ELEMENTS>
        <APPLICATION-SW-COMPONENT-TYPE>
          <SHORT-NAME>SwcB</SHORT-NAME>
          <PORTS>
            <R-PORT-PROTOTYPE>
              <SHORT-NAME>RpSpeed</SHORT-NAME>
              <REQUIRED-COM-SPECS>
                <NONQUEUED-RECEIVER-COM-SPEC>
                  <DATA-ELEMENT-REF DEST="VARIABLE-DATA-PROTOTYPE">/PortInterfaces/IfSpeed/Speed</DATA-ELEMENT-REF>
                  <INIT-VALUE>
                    <NUMERICAL-VALUE-SPECIFICATION><VALUE>0</VALUE></NUMERICAL-VALUE-SPECIFICATION>
                  </INIT-VALUE>
                </NONQUEUED-RECEIVER-COM-SPEC>
              </REQUIRED-COM-SPECS>
              <REQUIRED-INTERFACE-TREF DEST="SENDER-RECEIVER-INTERFACE">/PortInterfaces/IfSpeed</REQUIRED-INTERFACE-TREF>
            </R-PORT-PROTOTYPE>
            <P-PORT-PROTOTYPE>
              <SHORT-NAME>PpDiag</SHORT-NAME>
              <PROVIDED-COM-SPECS>
                <SERVER-COM-SPEC>
                  <OPERATION-REF DEST="CLIENT-SERVER-OPERATION">/PortInterfaces/IfDiag/ReadDtc</OPERATION-REF>
                  <QUEUE-LENGTH>1</QUEUE-LENGTH>
                </SERVER-COM-SPEC>
              </PROVIDED-COM-SPECS>
              <PROVIDED-INTERFACE-TREF DEST="CLIENT-SERVER-INTERFACE">/PortInterfaces/IfDiag</PROVIDED-INTERFACE-TREF>
            </P-PORT-PROTOTYPE>
            <R-PORT-PROTOTYPE>
              <SHORT-NAME>RpEvents</SHORT-NAME>
              <REQUIRED-COM-SPECS>
                <QUEUED-RECEIVER-COM-SPEC>
                  <DATA-ELEMENT-REF DEST="VARIABLE-DATA-PROTOTYPE">/PortInterfaces/IfEvents/Event</DATA-ELEMENT-REF>
                  <QUEUE-LENGTH>4</QUEUE-LENGTH>
                </QUEUED-RECEIVER-COM-SPEC>
              </REQUIRED-COM-SPECS>
              <REQUIRED-INTERFACE-TREF DEST="SENDER-RECEIVER-INTERFACE">/PortInterfaces/IfEvents</REQUIRED-INTERFACE-TREF>
            </R-PORT-PROTOTYPE>
          </PORTS>
          <INTERNAL-BEHAVIORS>
            <SWC-INTERNAL-BEHAVIOR>
              <SHORT-NAME>SwcB_InternalBehavior</SHORT-NAME>
              <EVENTS>
                <DATA-RECEIVED-EVENT>
                  <SHORT-NAME>DRT_RunB_OnSpeed</SHORT-NAME>
                  <START-ON-EVENT-REF DEST="RUNNABLE-ENTITY">/ComponentTypes/SwcB/SwcB_InternalBehavior/RunB_OnSpeed</START-ON-EVENT-REF>
                  <DATA-IREF>
                    <CONTEXT-R-PORT-REF DEST="R-PORT-PROTOTYPE">/ComponentTypes/SwcB/RpSpeed</CONTEXT-R-PORT-REF>
                    <TARGET-DATA-ELEMENT-REF DEST="VARIABLE-DATA-PROTOTYPE">/PortInterfaces/IfSpeed/Speed</TARGET-DATA-ELEMENT-REF>
                  </DATA-IREF>
                </DATA-RECEIVED-EVENT>
                <DATA-RECEIVE-ERROR-EVENT>
                  <SHORT-NAME>DRE_RunB_OnSpeedError</SHORT-NAME>
                  <START-ON-EVENT-REF DEST="RUNNABLE-ENTITY">/ComponentTypes/SwcB/SwcB_InternalBehavior/RunB_OnSpeed</START-ON-EVENT-REF>
                  <DATA-IREF>
                    <CONTEXT-R-PORT-REF DEST="R-PORT-PROTOTYPE">/ComponentTypes/SwcB/RpSpeed</CONTEXT-R-PORT-REF>
                    <TARGET-DATA-ELEMENT-REF DEST="VARIABLE-DATA-PROTOTYPE">/PortInterfaces/IfSpeed/Speed</TARGET-DATA-ELEMENT-REF>
                  </DATA-IREF>
                </DATA-RECEIVE-ERROR-EVENT>
                <OPERATION-INVOKED-EVENT>
                  <SHORT-NAME>OIT_PpDiag_ReadDtc</SHORT-NAME>
                  <START-ON-EVENT-REF DEST="RUNNABLE-ENTITY">/ComponentTypes/SwcB/SwcB_InternalBehavior/PpDiag_ReadDtc</START-ON-EVENT-REF>
                  <OPERATION-IREF>
                    <CONTEXT-P-PORT-REF DEST="P-PORT-PROTOTYPE">/ComponentTypes/SwcB/PpDiag</CONTEXT-P-PORT-REF>
                    <TARGET-PROVIDED-OPERATION-REF DEST="CLIENT-SERVER-OPERATION">/PortInterfaces/IfDiag/ReadDtc</TARGET-PROVIDED-OPERATION-REF>
                  </OPERATION-IREF>
                </OPERATION-INVOKED-EVENT>
                <BACKGROUND-EVENT>
                  <SHORT-NAME>BGT_RunB_Background</SHORT-NAME>
                  <START-ON-EVENT-REF DEST="RUNNABLE-ENTITY">/ComponentTypes/SwcB/SwcB_InternalBehavior/RunB_Background</START-ON-EVENT-REF>
                </BACKGROUND-EVENT>
              </EVENTS>
              <RUNNABLES>
                <RUNNABLE-ENTITY>
                  <SHORT-NAME>RunB_OnSpeed</SHORT-NAME>
                  <DATA-RECEIVE-POINT-BY-ARGUMENTS>
                    <VARIABLE-ACCESS>
                      <SHORT-NAME>DRPA_RpSpeed_Speed</SHORT-NAME>
                      <ACCESSED-VARIABLE>
                        <AUTOSAR-VARIABLE-IREF>
                          <PORT-PROTOTYPE-REF DEST="R-PORT-PROTOTYPE">/ComponentTypes/SwcB/RpSpeed</PORT-PROTOTYPE-REF>
                          <TARGET-DATA-PROTOTYPE-REF DEST="VARIABLE-DATA-PROTOTYPE">/PortInterfaces/IfSpeed/Speed</TARGET-DATA-PROTOTYPE-REF>
                        </AUTOSAR-VARIABLE-IREF>
                      </ACCESSED-VARIABLE>
                    </VARIABLE-ACCESS>
                    <VARIABLE-ACCESS>
                      <SHORT-NAME>DRPA_RpEvents_Event</SHORT-NAME>
                      <ACCESSED-VARIABLE>
                        <AUTOSAR-VARIABLE-IREF>
                          <PORT-PROTOTYPE-REF DEST="R-PORT-PROTOTYPE">/ComponentTypes/SwcB/RpEvents</PORT-PROTOTYPE-REF>
                          <TARGET-DATA-PROTOTYPE-REF DEST="VARIABLE-DATA-PROTOTYPE">/PortInterfaces/IfEvents/Event</TARGET-DATA-PROTOTYPE-REF>
                        </AUTOSAR-VARIABLE-IREF>
                      </ACCESSED-VARIABLE>
                    </VARIABLE-ACCESS>
                  </DATA-RECEIVE-POINT-BY-ARGUMENTS>
                  <SYMBOL>RunB_OnSpeed</SYMBOL>
                </RUNNABLE-ENTITY>
                <RUNNABLE-ENTITY>
                  <SHORT-NAME>PpDiag_ReadDtc</SHORT-NAME>
                  <CAN-BE-INVOKED-CONCURRENTLY>true</CAN-BE-INVOKED-CONCURRENTLY>
                  <SYMBOL>PpDiag_ReadDtc</SYMBOL>
                </RUNNABLE-ENTITY>
                <RUNNABLE-ENTITY>
                  <SHORT-NAME>RunB_Background</SHORT-NAME>
                  <DATA-READ-ACCESSS>
                    <VARIABLE-ACCESS>
                      <SHORT-NAME>DRA_RpSpeed_Speed</SHORT-NAME>
                      <ACCESSED-VARIABLE>
                        <AUTOSAR-VARIABLE-IREF>
                          <PORT-PROTOTYPE-REF DEST="R-PORT-PROTOTYPE">/ComponentTypes/SwcB/RpSpeed</PORT-PROTOTYPE-REF>
                          <TARGET-DATA-PROTOTYPE-REF DEST="VARIABLE-DATA-PROTOTYPE">/PortInterfaces/IfSpeed/Speed</TARGET-DATA-PROTOTYPE-REF>
                        </AUTOSAR-VARIABLE-IREF>
                      </ACCESSED-VARIABLE>
                    </VARIABLE-ACCESS>
                  </DATA-READ-ACCESSS>
                  <DATA-RECEIVE-POINT-BY-VALUES>
                    <VARIABLE-ACCESS>
                      <SHORT-NAME>DRPV_RpSpeed_Speed</SHORT-NAME>
                      <ACCESSED-VARIABLE>
                        <AUTOSAR-VARIABLE-IREF>
                          <PORT-PROTOTYPE-REF DEST="R-PORT-PROTOTYPE">/ComponentTypes/SwcB/RpSpeed</PORT-PROTOTYPE-REF>
                          <TARGET-DATA-PROTOTYPE-REF DEST="VARIABLE-DATA-PROTOTYPE">/PortInterfaces/IfSpeed/Speed</TARGET-DATA-PROTOTYPE-REF>
                        </AUTOSAR-VARIABLE-IREF>
                      </ACCESSED-VARIABLE>
                    </VARIABLE-ACCESS>
                  </DATA-RECEIVE-POINT-BY-VALUES>
                  <SYMBOL>RunB_Background</SYMBOL>
                </RUNNABLE-ENTITY>
              </RUNNABLES>
            </SWC-INTERNAL-BEHAVIOR>
          </INTERNAL-BEHAVIORS>
        </APPLICATION-SW-COMPONENT-TYPE>
      </ELEMENTS>
    </AR-PACKAGE>
  </AR-PACKAGES>
</AUTOSAR>
"""

SWC_M = doc(
    package(
        "ComponentTypes",
        swc("SwcM", ports=p_port("PpTorque", f"{IF}/IfTorque")),
    )
)

OTHER_KINDS = f"""<?xml version="1.0" encoding="UTF-8"?>
<AUTOSAR xmlns="{NS}" xmlns:xsi="{XSI}" xsi:schemaLocation="{NS} AUTOSAR_4-2-2.xsd">
  <AR-PACKAGES>
    <AR-PACKAGE>
      <SHORT-NAME>ComponentTypes</SHORT-NAME>
      <ELEMENTS>
        <SENSOR-ACTUATOR-SW-COMPONENT-TYPE><SHORT-NAME>SensorSwc</SHORT-NAME></SENSOR-ACTUATOR-SW-COMPONENT-TYPE>
        <COMPLEX-DEVICE-DRIVER-SW-COMPONENT-TYPE><SHORT-NAME>CddSwc</SHORT-NAME></COMPLEX-DEVICE-DRIVER-SW-COMPONENT-TYPE>
        <ECU-ABSTRACTION-SW-COMPONENT-TYPE><SHORT-NAME>IoHwAb</SHORT-NAME></ECU-ABSTRACTION-SW-COMPONENT-TYPE>
        <SERVICE-SW-COMPONENT-TYPE><SHORT-NAME>NvM</SHORT-NAME></SERVICE-SW-COMPONENT-TYPE>
        <SERVICE-PROXY-SW-COMPONENT-TYPE><SHORT-NAME>DiagProxy</SHORT-NAME></SERVICE-PROXY-SW-COMPONENT-TYPE>
        <NV-BLOCK-SW-COMPONENT-TYPE>
          <SHORT-NAME>NvBlocks</SHORT-NAME>
          <PORTS>
            <P-PORT-PROTOTYPE>
              <SHORT-NAME>PpNv</SHORT-NAME>
              <PROVIDED-COM-SPECS>
                <NV-PROVIDE-COM-SPEC>
                  <RAM-BLOCK-INIT-VALUE>
                    <NUMERICAL-VALUE-SPECIFICATION><VALUE>5</VALUE></NUMERICAL-VALUE-SPECIFICATION>
                  </RAM-BLOCK-INIT-VALUE>
                  <VARIABLE-REF DEST="VARIABLE-DATA-PROTOTYPE">/PortInterfaces/IfNv/Stored</VARIABLE-REF>
                </NV-PROVIDE-COM-SPEC>
              </PROVIDED-COM-SPECS>
              <PROVIDED-INTERFACE-TREF DEST="NV-DATA-INTERFACE">/PortInterfaces/IfNv</PROVIDED-INTERFACE-TREF>
            </P-PORT-PROTOTYPE>
          </PORTS>
        </NV-BLOCK-SW-COMPONENT-TYPE>
        <PARAMETER-SW-COMPONENT-TYPE>
          <SHORT-NAME>CalSwc</SHORT-NAME>
          <PORTS>
            <P-PORT-PROTOTYPE>
              <SHORT-NAME>PpCal</SHORT-NAME>
              <PROVIDED-COM-SPECS>
                <PARAMETER-PROVIDE-COM-SPEC>
                  <INIT-VALUE>
                    <NUMERICAL-VALUE-SPECIFICATION><VALUE>100</VALUE></NUMERICAL-VALUE-SPECIFICATION>
                  </INIT-VALUE>
                  <PARAMETER-REF DEST="PARAMETER-DATA-PROTOTYPE">/PortInterfaces/IfCal/Gain</PARAMETER-REF>
                </PARAMETER-PROVIDE-COM-SPEC>
              </PROVIDED-COM-SPECS>
              <PROVIDED-INTERFACE-TREF DEST="PARAMETER-INTERFACE">/PortInterfaces/IfCal</PROVIDED-INTERFACE-TREF>
            </P-PORT-PROTOTYPE>
          </PORTS>
        </PARAMETER-SW-COMPONENT-TYPE>
      </ELEMENTS>
    </AR-PACKAGE>
  </AR-PACKAGES>
</AUTOSAR>
"""

COMPOSITIONS = f"""<?xml version="1.0" encoding="UTF-8"?>
<AUTOSAR xmlns="{NS}" xmlns:xsi="{XSI}" xsi:schemaLocation="{NS} AUTOSAR_4-2-2.xsd">
  <AR-PACKAGES>
    <AR-PACKAGE>
      <SHORT-NAME>ComponentTypes</SHORT-NAME>
      <ELEMENTS>
        <COMPOSITION-SW-COMPONENT-TYPE UUID="cccccccc-0000-0000-0000-000000000001">
          <SHORT-NAME>EcuComposition</SHORT-NAME>
          <COMPONENTS>
            <SW-COMPONENT-PROTOTYPE UUID="cccccccc-0000-0000-0000-0000000000a1">
              <SHORT-NAME>SwcA</SHORT-NAME>
              <TYPE-TREF DEST="APPLICATION-SW-COMPONENT-TYPE">/ComponentTypes/SwcA</TYPE-TREF>
            </SW-COMPONENT-PROTOTYPE>
            <SW-COMPONENT-PROTOTYPE>
              <SHORT-NAME>SwcB</SHORT-NAME>
              <TYPE-TREF DEST="APPLICATION-SW-COMPONENT-TYPE">/ComponentTypes/SwcB</TYPE-TREF>
            </SW-COMPONENT-PROTOTYPE>
            <SW-COMPONENT-PROTOTYPE>
              <SHORT-NAME>SwcM</SHORT-NAME>
              <TYPE-TREF DEST="APPLICATION-SW-COMPONENT-TYPE">/ComponentTypes/SwcM</TYPE-TREF>
            </SW-COMPONENT-PROTOTYPE>
            <SW-COMPONENT-PROTOTYPE>
              <SHORT-NAME>Cluster</SHORT-NAME>
              <TYPE-TREF DEST="COMPOSITION-SW-COMPONENT-TYPE">/ComponentTypes/Cluster</TYPE-TREF>
            </SW-COMPONENT-PROTOTYPE>
          </COMPONENTS>
          <CONNECTORS>
            <ASSEMBLY-SW-CONNECTOR UUID="cccccccc-0000-0000-0000-0000000000c1">
              <SHORT-NAME>SwcA_PpSpeed_SwcB_RpSpeed</SHORT-NAME>
              <PROVIDER-IREF>
                <CONTEXT-COMPONENT-REF DEST="SW-COMPONENT-PROTOTYPE">/ComponentTypes/EcuComposition/SwcA</CONTEXT-COMPONENT-REF>
                <TARGET-P-PORT-REF DEST="P-PORT-PROTOTYPE">/ComponentTypes/SwcA/PpSpeed</TARGET-P-PORT-REF>
              </PROVIDER-IREF>
              <REQUESTER-IREF>
                <CONTEXT-COMPONENT-REF DEST="SW-COMPONENT-PROTOTYPE">/ComponentTypes/EcuComposition/SwcB</CONTEXT-COMPONENT-REF>
                <TARGET-R-PORT-REF DEST="R-PORT-PROTOTYPE">/ComponentTypes/SwcB/RpSpeed</TARGET-R-PORT-REF>
              </REQUESTER-IREF>
            </ASSEMBLY-SW-CONNECTOR>
            <ASSEMBLY-SW-CONNECTOR>
              <SHORT-NAME>SwcB_PpDiag_SwcA_RpDiag</SHORT-NAME>
              <PROVIDER-IREF>
                <CONTEXT-COMPONENT-REF DEST="SW-COMPONENT-PROTOTYPE">/ComponentTypes/EcuComposition/SwcB</CONTEXT-COMPONENT-REF>
                <TARGET-P-PORT-REF DEST="P-PORT-PROTOTYPE">/ComponentTypes/SwcB/PpDiag</TARGET-P-PORT-REF>
              </PROVIDER-IREF>
              <REQUESTER-IREF>
                <CONTEXT-COMPONENT-REF DEST="SW-COMPONENT-PROTOTYPE">/ComponentTypes/EcuComposition/SwcA</CONTEXT-COMPONENT-REF>
                <TARGET-R-PORT-REF DEST="R-PORT-PROTOTYPE">/ComponentTypes/SwcA/RpDiag</TARGET-R-PORT-REF>
              </REQUESTER-IREF>
            </ASSEMBLY-SW-CONNECTOR>
            <ASSEMBLY-SW-CONNECTOR>
              <SHORT-NAME>SwcA_PpEvents_SwcB_RpEvents</SHORT-NAME>
              <PROVIDER-IREF>
                <CONTEXT-COMPONENT-REF DEST="SW-COMPONENT-PROTOTYPE">/ComponentTypes/EcuComposition/SwcA</CONTEXT-COMPONENT-REF>
                <TARGET-P-PORT-REF DEST="P-PORT-PROTOTYPE">/ComponentTypes/SwcA/PpEvents</TARGET-P-PORT-REF>
              </PROVIDER-IREF>
              <REQUESTER-IREF>
                <CONTEXT-COMPONENT-REF DEST="SW-COMPONENT-PROTOTYPE">/ComponentTypes/EcuComposition/SwcB</CONTEXT-COMPONENT-REF>
                <TARGET-R-PORT-REF DEST="R-PORT-PROTOTYPE">/ComponentTypes/SwcB/RpEvents</TARGET-R-PORT-REF>
              </REQUESTER-IREF>
            </ASSEMBLY-SW-CONNECTOR>
            <ASSEMBLY-SW-CONNECTOR>
              <SHORT-NAME>SwcA_PpSpeed_Cluster_RpSpeedIn</SHORT-NAME>
              <PROVIDER-IREF>
                <CONTEXT-COMPONENT-REF DEST="SW-COMPONENT-PROTOTYPE">/ComponentTypes/EcuComposition/SwcA</CONTEXT-COMPONENT-REF>
                <TARGET-P-PORT-REF DEST="P-PORT-PROTOTYPE">/ComponentTypes/SwcA/PpSpeed</TARGET-P-PORT-REF>
              </PROVIDER-IREF>
              <REQUESTER-IREF>
                <CONTEXT-COMPONENT-REF DEST="SW-COMPONENT-PROTOTYPE">/ComponentTypes/EcuComposition/Cluster</CONTEXT-COMPONENT-REF>
                <TARGET-R-PORT-REF DEST="R-PORT-PROTOTYPE">/ComponentTypes/Cluster/RpSpeedIn</TARGET-R-PORT-REF>
              </REQUESTER-IREF>
            </ASSEMBLY-SW-CONNECTOR>
          </CONNECTORS>
        </COMPOSITION-SW-COMPONENT-TYPE>
        <COMPOSITION-SW-COMPONENT-TYPE>
          <SHORT-NAME>Cluster</SHORT-NAME>
          <PORTS>
            <R-PORT-PROTOTYPE>
              <SHORT-NAME>RpSpeedIn</SHORT-NAME>
              <REQUIRED-INTERFACE-TREF DEST="SENDER-RECEIVER-INTERFACE">/PortInterfaces/IfSpeed</REQUIRED-INTERFACE-TREF>
            </R-PORT-PROTOTYPE>
            <P-PORT-PROTOTYPE>
              <SHORT-NAME>PpDiagOut</SHORT-NAME>
              <PROVIDED-INTERFACE-TREF DEST="CLIENT-SERVER-INTERFACE">/PortInterfaces/IfDiag</PROVIDED-INTERFACE-TREF>
            </P-PORT-PROTOTYPE>
          </PORTS>
          <COMPONENTS>
            <SW-COMPONENT-PROTOTYPE>
              <SHORT-NAME>SwcB1</SHORT-NAME>
              <TYPE-TREF DEST="APPLICATION-SW-COMPONENT-TYPE">/ComponentTypes/SwcB</TYPE-TREF>
            </SW-COMPONENT-PROTOTYPE>
            <SW-COMPONENT-PROTOTYPE>
              <SHORT-NAME>SwcB2</SHORT-NAME>
              <TYPE-TREF DEST="APPLICATION-SW-COMPONENT-TYPE">/ComponentTypes/SwcB</TYPE-TREF>
            </SW-COMPONENT-PROTOTYPE>
          </COMPONENTS>
          <CONNECTORS>
            <DELEGATION-SW-CONNECTOR>
              <SHORT-NAME>RpSpeedIn_SwcB1_RpSpeed</SHORT-NAME>
              <INNER-PORT-IREF>
                <R-PORT-IN-COMPOSITION-INSTANCE-REF>
                  <CONTEXT-COMPONENT-REF DEST="SW-COMPONENT-PROTOTYPE">/ComponentTypes/Cluster/SwcB1</CONTEXT-COMPONENT-REF>
                  <TARGET-R-PORT-REF DEST="R-PORT-PROTOTYPE">/ComponentTypes/SwcB/RpSpeed</TARGET-R-PORT-REF>
                </R-PORT-IN-COMPOSITION-INSTANCE-REF>
              </INNER-PORT-IREF>
              <OUTER-PORT-REF DEST="R-PORT-PROTOTYPE">/ComponentTypes/Cluster/RpSpeedIn</OUTER-PORT-REF>
            </DELEGATION-SW-CONNECTOR>
            <DELEGATION-SW-CONNECTOR>
              <SHORT-NAME>SwcB2_PpDiag_PpDiagOut</SHORT-NAME>
              <INNER-PORT-IREF>
                <P-PORT-IN-COMPOSITION-INSTANCE-REF>
                  <CONTEXT-COMPONENT-REF DEST="SW-COMPONENT-PROTOTYPE">/ComponentTypes/Cluster/SwcB2</CONTEXT-COMPONENT-REF>
                  <TARGET-P-PORT-REF DEST="P-PORT-PROTOTYPE">/ComponentTypes/SwcB/PpDiag</TARGET-P-PORT-REF>
                </P-PORT-IN-COMPOSITION-INSTANCE-REF>
              </INNER-PORT-IREF>
              <OUTER-PORT-REF DEST="P-PORT-PROTOTYPE">/ComponentTypes/Cluster/PpDiagOut</OUTER-PORT-REF>
            </DELEGATION-SW-CONNECTOR>
          </CONNECTORS>
        </COMPOSITION-SW-COMPONENT-TYPE>
      </ELEMENTS>
    </AR-PACKAGE>
  </AR-PACKAGES>
</AUTOSAR>
"""


def ecuc_mapping(name: str, event: str, task: str | None, position: int | None, vendor: str) -> str:
    """One RteEventToTaskMapping container. ``task`` None leaves the event unmapped."""
    base = f"{vendor}/Rte/RteSwComponentInstance/RteEventToTaskMapping"
    params = ""
    if position is not None:
        params = (
            "<PARAMETER-VALUES><ECUC-NUMERICAL-PARAM-VALUE>"
            f'<DEFINITION-REF DEST="ECUC-INTEGER-PARAM-DEF">{base}/RtePositionInTask</DEFINITION-REF>'
            f"<VALUE>{position}</VALUE></ECUC-NUMERICAL-PARAM-VALUE></PARAMETER-VALUES>"
        )
    refs = (
        "<ECUC-REFERENCE-VALUE>"
        f'<DEFINITION-REF DEST="ECUC-FOREIGN-REFERENCE-DEF">{base}/RteEventRef</DEFINITION-REF>'
        f'<VALUE-REF DEST="RTE-EVENT">{event}</VALUE-REF></ECUC-REFERENCE-VALUE>'
    )
    if task is not None:
        refs += (
            "<ECUC-REFERENCE-VALUE>"
            f'<DEFINITION-REF DEST="ECUC-REFERENCE-DEF">{base}/RteMappedToTaskRef</DEFINITION-REF>'
            f'<VALUE-REF DEST="ECUC-CONTAINER-VALUE">/ActiveEcuC/Os/{task}</VALUE-REF>'
            "</ECUC-REFERENCE-VALUE>"
        )
    return (
        f"<ECUC-CONTAINER-VALUE><SHORT-NAME>{name}</SHORT-NAME>"
        f'<DEFINITION-REF DEST="ECUC-PARAM-CONF-CONTAINER-DEF">{base}</DEFINITION-REF>'
        f"{params}<REFERENCE-VALUES>{refs}</REFERENCE-VALUES></ECUC-CONTAINER-VALUE>"
    )


def ecuc_instance(name: str, mappings: str, vendor: str) -> str:
    """One RteSwComponentInstance container holding event mappings."""
    return (
        f"<ECUC-CONTAINER-VALUE><SHORT-NAME>{name}</SHORT-NAME>"
        f'<DEFINITION-REF DEST="ECUC-PARAM-CONF-CONTAINER-DEF">{vendor}/Rte/RteSwComponentInstance'
        f"</DEFINITION-REF><SUB-CONTAINERS>{mappings}</SUB-CONTAINERS></ECUC-CONTAINER-VALUE>"
    )


def ecuc_task(name: str, vendor: str) -> str:
    return (
        f"<ECUC-CONTAINER-VALUE><SHORT-NAME>{name}</SHORT-NAME>"
        f'<DEFINITION-REF DEST="ECUC-PARAM-CONF-CONTAINER-DEF">{vendor}/Os/OsTask</DEFINITION-REF>'
        "<PARAMETER-VALUES><ECUC-NUMERICAL-PARAM-VALUE>"
        f'<DEFINITION-REF DEST="ECUC-INTEGER-PARAM-DEF">{vendor}/Os/OsTask/OsTaskPriority'
        "</DEFINITION-REF><VALUE>10</VALUE></ECUC-NUMERICAL-PARAM-VALUE></PARAMETER-VALUES>"
        "</ECUC-CONTAINER-VALUE>"
    )


def ecuc_module(name: str, containers: str, vendor: str) -> str:
    return (
        f"<ECUC-MODULE-CONFIGURATION-VALUES><SHORT-NAME>{name}</SHORT-NAME>"
        f'<DEFINITION-REF DEST="ECUC-MODULE-DEF">{vendor}/{name}</DEFINITION-REF>'
        f"<CONTAINERS>{containers}</CONTAINERS></ECUC-MODULE-CONFIGURATION-VALUES>"
    )


def ecuc_doc(vendor: str = "/MICROSAR") -> str:
    """The reference ECUC file. ``vendor`` is the prefix of every definition path."""
    os_module = ecuc_module(
        "Os",
        ecuc_task("Task_10ms", vendor)
        + ecuc_task("Task_Event", vendor)
        + ecuc_task("Task_Init", vendor)
        # an alarm is an Os container that is not a task
        + "<ECUC-CONTAINER-VALUE><SHORT-NAME>Alarm_10ms</SHORT-NAME>"
        f'<DEFINITION-REF DEST="ECUC-PARAM-CONF-CONTAINER-DEF">{vendor}/Os/OsAlarm</DEFINITION-REF>'
        "</ECUC-CONTAINER-VALUE>",
        vendor,
    )
    on_speed = f"{BEH_B}/DRT_RunB_OnSpeed"
    rte_module = ecuc_module(
        "Rte",
        ecuc_instance(
            "SwcA",
            ecuc_mapping("TMT_RunA_10ms", f"{BEH_A}/TMT_RunA_10ms", "Task_10ms", 1, vendor)
            + ecuc_mapping("IT_RunA_Init", f"{BEH_A}/IT_RunA_Init", "Task_Init", None, vendor),
            vendor,
        )
        + ecuc_instance(
            "SwcB",
            ecuc_mapping("DRT_RunB_OnSpeed", on_speed, "Task_Event", 2, vendor)
            # a server runnable called directly: the event exists but has no task
            + ecuc_mapping("OIT_PpDiag_ReadDtc", f"{BEH_B}/OIT_PpDiag_ReadDtc", None, None, vendor),
            vendor,
        )
        # SwcB is instantiated twice more inside Cluster: same event, same task
        + ecuc_instance(
            "SwcB1", ecuc_mapping("DRT_RunB_OnSpeed", on_speed, "Task_Event", 3, vendor), vendor
        )
        + ecuc_instance(
            "SwcB2", ecuc_mapping("DRT_RunB_OnSpeed", on_speed, "Task_Event", 4, vendor), vendor
        ),
        vendor,
    )
    return doc(package("ActiveEcuC", os_module, rte_module))


SAMPLE_FILES: dict[str, str] = {
    F_TYPES: DATA_TYPES,
    F_INTERFACES: INTERFACES,
    F_SWC_A: SWC_A,
    F_SWC_B: SWC_B,
    F_SWC_M: SWC_M,
    F_OTHER: OTHER_KINDS,
    F_COMPOSITIONS: COMPOSITIONS,
}


def write_sample_repo(root: Path, *, ecuc: bool = True) -> None:
    """Write the reference repository below ``root``."""
    for rel, text in SAMPLE_FILES.items():
        write(root, rel, text)
    if ecuc:
        write(root, F_ECUC, ecuc_doc())
