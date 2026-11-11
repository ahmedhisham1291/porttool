"""Finding codes of the validator. One constant per failure, one line of description each.

The codes are stable: tests, the Results sheet and the user guide refer to them. Add new
codes at the end of a group; never reuse or renumber one.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from porttool.core.results import Severity


@dataclass(frozen=True, slots=True)
class CodeInfo:
    code: str
    severity: Severity
    description: str


_REGISTRY: dict[str, CodeInfo] = {}


def _code(code: str, description: str, severity: Severity = Severity.ERROR) -> str:
    if code in _REGISTRY:  # pragma: no cover - guards against a copy-paste mistake
        raise ValueError(f"duplicate finding code {code}")
    _REGISTRY[code] = CodeInfo(code=code, severity=severity, description=description)
    return code


_W = Severity.WARNING
_I = Severity.INFO

# ---- row level
ROW_UNKNOWN_SHEET = _code("VAL-ROW-001", "The row comes from a sheet that takes no requests.")
ROW_UNKNOWN_ACTION = _code("VAL-ROW-002", "The Action cell is empty or not ADD, MODIFY or DELETE.")
ROW_BLANK_CELL = _code("VAL-ROW-003", "A required cell is empty.")
ROW_ADD_WITH_ROWID = _code("VAL-ROW-004", "Action ADD on a row that describes an existing object.")
ROW_CHANGE_WITHOUT_ROWID = _code("VAL-ROW-005", "Action MODIFY or DELETE on a new row (no RowID).")
ROW_STALE = _code(
    "VAL-ROW-006", "The object of this row no longer exists in the repo (stale workbook baseline)."
)
ROW_STALE_ROWID = _code(
    "VAL-ROW-007", "The RowID is outdated; the object was found by its names instead.", _W
)
ROW_UNSUPPORTED = _code("VAL-ROW-008", "MODIFY or DELETE is not supported for this sheet in v1.")
ROW_IDENTITY_CHANGED = _code(
    "VAL-ROW-009", "Identity cells differ from the object of the RowID (rename or move)."
)
ROW_BAD_CELL = _code(
    "VAL-ROW-010", "A cell does not have the expected format (number, flag, list)."
)

# ---- names
NAME_SYNTAX = _code("VAL-NAME-001", "The name is not a valid AUTOSAR short name.")
NAME_TOO_LONG = _code("VAL-NAME-002", "The name is longer than the configured maximum length.")
NAME_PATTERN = _code("VAL-NAME-003", "The name does not match the project naming rule.")
NAME_C_KEYWORD = _code("VAL-NAME-004", "The name is a C keyword.")
NAME_DUPLICATE = _code("VAL-NAME-005", "An object with this name already exists in the same scope.")
NAME_CASE_CLASH = _code(
    "VAL-NAME-006", "The name differs only by letter case from another name in the same scope."
)

# ---- references
REF_UNKNOWN_SWC = _code("VAL-REF-001", "The SWC does not exist.")
REF_AMBIGUOUS_SWC = _code("VAL-REF-002", "Several SWCs have this short name.")
REF_UNKNOWN_INTERFACE = _code("VAL-REF-003", "The port interface does not exist.")
REF_AMBIGUOUS_INTERFACE = _code("VAL-REF-004", "Several port interfaces have this short name.")
REF_UNKNOWN_DATA_TYPE = _code("VAL-REF-005", "The data type does not exist.")
REF_AMBIGUOUS_DATA_TYPE = _code("VAL-REF-006", "Several data types have this short name.")
REF_UNKNOWN_COMPOSITION = _code("VAL-REF-007", "The composition does not exist.")
REF_AMBIGUOUS_COMPOSITION = _code("VAL-REF-008", "Several compositions have this short name.")
REF_UNKNOWN_PROTOTYPE = _code("VAL-REF-009", "The composition has no component with this name.")
REF_UNKNOWN_PORT = _code("VAL-REF-010", "The SWC has no port with this name.")
REF_UNKNOWN_RUNNABLE = _code("VAL-REF-011", "The SWC has no runnable with this name.")
REF_UNKNOWN_TARGET = _code(
    "VAL-REF-012", "The element, operation or argument is not part of the port's interface."
)
REF_REJECTED_DEPENDENCY = _code(
    "VAL-REF-013", "The request needs another request that was rejected."
)
REF_PORT_WITHOUT_INTERFACE = _code(
    "VAL-REF-014",
    "The interface of the port is missing in the repo, so the port cannot be checked.",
)

# ---- ownership
OWN_LOCKED = _code("VAL-OWN-001", "The SWC is locked and is never edited.")
OWN_SERVICE = _code("VAL-OWN-002", "Service SWCs are read-only.")
OWN_COMPOSITION = _code("VAL-OWN-003", "A composition used as SWC is read-only.")
OWN_COMPOSITION_LOCKED = _code(
    "VAL-OWN-004", "Connectors of this composition cannot be edited (locked or model-owned)."
)

# ---- data types
DT_LEVEL = _code("VAL-DT-001", "The level is not application or implementation.")
DT_CATEGORY = _code("VAL-DT-002", "The data type category is unknown or not supported in v1.")
DT_BASE_TYPE = _code("VAL-DT-003", "The base type does not exist.")
DT_RANGE_ORDER = _code("VAL-DT-004", "The lower limit is greater than the upper limit.")
DT_RANGE_FIT = _code("VAL-DT-005", "The range or an enumeration value does not fit the base type.")
DT_ENUM_VALUE_DUPLICATE = _code("VAL-DT-006", "An enumeration value is used twice.")
DT_ENUM_LABEL_DUPLICATE = _code("VAL-DT-007", "An enumeration label is used twice.")
DT_ARRAY_SIZE = _code("VAL-DT-008", "The array size is not a positive whole number.")
DT_RECORD_DUPLICATE = _code("VAL-DT-009", "A record element name is used twice.")
DT_CYCLE = _code("VAL-DT-010", "The data type contains itself (circular definition).")
DT_CELL_NOT_USED = _code("VAL-DT-011", "A cell is filled that does not apply to this category.")
DT_UNIT = _code("VAL-DT-012", "The unit does not exist.")
DT_SCALING = _code("VAL-DT-013", "The linear scaling is invalid (factor zero).")
DT_LEVEL_MIX = _code("VAL-DT-014", "An element type has another level than the type that uses it.")

# ---- interfaces
IF_EMPTY = _code("VAL-IF-001", "The interface has no element and no operation.")
IF_KIND = _code("VAL-IF-002", "The interface kind is unknown or cannot be created in v1.")
IF_MODE_GROUP = _code(
    "VAL-IF-003", "The mode group of the mode-switch interface cannot be resolved."
)
IF_EXISTING = _code("VAL-IF-004", "An existing interface cannot be changed in v1.")
IF_ELEMENT_DUPLICATE = _code("VAL-IF-005", "Two rows define the same element differently.")
IF_ARGUMENT_DUPLICATE = _code("VAL-IF-006", "Two rows define the same argument differently.")
IF_ARGUMENT_DIRECTION = _code("VAL-IF-007", "The argument direction is not IN, OUT or INOUT.")
IF_CELL_NOT_USED = _code(
    "VAL-IF-008", "A cell is filled that does not apply to this interface kind."
)
IF_GROUP_REJECTED = _code(
    "VAL-IF-009", "The interface is not created because another row of it was rejected."
)

# ---- ports
PORT_DIRECTION = _code("VAL-PORT-001", "The port direction is not P, R or PR.")
PORT_PR_KIND = _code("VAL-PORT-002", "PR ports need a sender-receiver style interface.")
PORT_INIT_MISSING = _code(
    "VAL-PORT-003", "A non-queued data element has neither an init value nor an explicit none."
)
PORT_INIT_FORMAT = _code("VAL-PORT-004", "The init value cell cannot be matched to the elements.")
PORT_INIT_PARSE = _code("VAL-PORT-005", "The init value is not a value of the element's data type.")
PORT_INIT_RANGE = _code("VAL-PORT-006", "The init value is outside the range of the data type.")
PORT_INIT_NOT_USED = _code("VAL-PORT-007", "An init value is given where none can be stored.")
PORT_INIT_COMPOSITE = _code(
    "VAL-PORT-008", "Init values of array and record types are not supported in v1 (use none)."
)
PORT_IN_USE = _code("VAL-PORT-009", "The port is still used and cannot be deleted.")
PORT_MODIFY_BREAKS = _code("VAL-PORT-010", "The new interface does not fit the existing use.")
PORT_INIT_DIFFERS = _code(
    "VAL-PORT-011", "The port exists as requested, but with another init value.", _W
)

# ---- connectors
CON_SIDE = _code("VAL-CON-001", "A connector needs a P or PR port as provider and an R or PR port.")
CON_SELF = _code("VAL-CON-002", "A port cannot be connected to itself.")
CON_INCOMPATIBLE = _code("VAL-CON-003", "The interfaces of the two ports are not compatible.")
CON_DUPLICATE = _code("VAL-CON-004", "The two ports are already connected by another connector.")
CON_NO_COMPOSITION = _code("VAL-CON-005", "No composition contains both components.")
CON_MANY_COMPOSITIONS = _code("VAL-CON-006", "More than one composition contains both components.")

# ---- multiplicity
MUL_N_TO_M = _code("VAL-MUL-001", "Sender-receiver communication would become n:m.")
MUL_CLIENT = _code("VAL-MUL-002", "A client port can be connected to one server only.")
MUL_SINGLE_PROVIDER = _code("VAL-MUL-003", "This kind of requester port accepts one provider only.")
MUL_N_TO_ONE = _code("VAL-MUL-004", "Several senders feed one receiver (n:1).", _W)

# ---- access points
ACC_KIND = _code("VAL-ACC-001", "The access kind is unknown or not supported in v1.")
ACC_FIT = _code("VAL-ACC-002", "The access kind does not fit the port direction or interface kind.")
ACC_QUEUED = _code("VAL-ACC-003", "The access kind does not fit the queued setting of the element.")
ACC_DUPLICATE = _code("VAL-ACC-004", "The runnable already accesses this target in another way.")

# ---- triggers
TRG_KIND = _code("VAL-TRG-001", "The trigger kind is unknown or not supported in v1.")
TRG_PERIOD = _code("VAL-TRG-002", "A periodic trigger needs a period greater than zero.")
TRG_CELL_NOT_USED = _code(
    "VAL-TRG-003", "A cell is filled that does not apply to this trigger kind."
)
TRG_FIT = _code("VAL-TRG-004", "The trigger kind does not fit the port.")
TRG_TASK = _code("VAL-TRG-005", "The task does not exist.")
TRG_SERVER_TAKEN = _code("VAL-TRG-006", "The server operation already has a runnable.")
TRG_TASK_AMBIGUOUS = _code(
    "VAL-TRG-007", "The runnable runs in several tasks; the Task cell must name one."
)
TRG_SERVER_ORPHAN = _code(
    "VAL-TRG-008", "Deleting the trigger would leave a server operation without a runnable."
)
TRG_TASK_DIFFERS = _code(
    "VAL-TRG-009", "The trigger exists as requested, but is mapped to another task.", _W
)

# ---- conflicts between requests
CFL_MERGED = _code("VAL-CFL-001", "The same request appears more than once and was merged.", _I)
CFL_DIFFERENT = _code("VAL-CFL-002", "Several rows request the same object differently.")
CFL_ADD_DELETE = _code(
    "VAL-CFL-003", "One object is added or changed and deleted in the same import."
)
CFL_DELETED_PORT = _code("VAL-CFL-004", "A request uses a port that another row deletes.")
CFL_PENDING = _code("VAL-CFL-005", "The request differs from a request in the pending queue.")

# ---- pending queue
PEND_FULFILLED = _code("VAL-PEND-001", "A pending request is fulfilled by the repo and closed.", _I)
PEND_MISMATCH = _code(
    "VAL-PEND-002", "The awaited object exists, but not as requested; the entry is kept."
)
PEND_RELEASED = _code("VAL-PEND-003", "A held connector is released because its ports exist.", _I)
PEND_STALE = _code("VAL-PEND-004", "A pending request is older than the configured limit.", _W)
PEND_RELEASE_FAILED = _code(
    "VAL-PEND-005", "A held connector cannot be released because it is no longer valid."
)
PEND_ORPHAN = _code(
    "VAL-PEND-006", "A pending request cannot be checked because its target is gone.", _W
)
PEND_ALREADY_QUEUED = _code("VAL-PEND-007", "The request is already in the pending queue.", _I)

# ---- configuration
CFG_NAME_REGEX = _code(
    "VAL-CFG-001", "A naming rule of the project is not a valid regular expression."
)
CFG_NAME_PATTERN = _code("VAL-CFG-002", "A name pattern of the project cannot be filled in.")

#: Every code with its default severity and description, in definition order.
CODES: Mapping[str, CodeInfo] = MappingProxyType(_REGISTRY)

#: All code strings, in definition order.
ALL_CODES: tuple[str, ...] = tuple(_REGISTRY)


def describe(code: str) -> str:
    """One-line description of ``code`` (empty string for an unknown code)."""
    info = CODES.get(code)
    return info.description if info is not None else ""


def severity_of(code: str) -> Severity:
    """Default severity of ``code``; ERROR for an unknown code."""
    info = CODES.get(code)
    return info.severity if info is not None else Severity.ERROR


def format_code_table() -> str:
    """The code table as Markdown, as printed by the ``validator-codes`` command."""
    lines = ["| Code | Severity | Meaning |", "| --- | --- | --- |"]
    lines.extend(
        f"| {info.code} | {info.severity.value} | {info.description} |" for info in CODES.values()
    )
    return "\n".join(lines)
