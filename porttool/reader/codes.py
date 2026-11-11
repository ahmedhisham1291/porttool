"""Stable finding codes of the reader. Every code starts with ``RDR-``.

Codes are part of the public API: other packages and the user guide refer to them.
Never reuse a retired number.
"""

from __future__ import annotations

# ---- files
FILE_VANISHED = "RDR-FILE-001"  # found by the scan, gone before it could be read
FILE_UNREADABLE = "RDR-FILE-002"  # permission denied, sharing violation, other OS error
FILE_UNSTABLE = "RDR-FILE-003"  # kept changing while it was read
ROOT_MISSING = "RDR-FILE-004"  # a configured ARXML root or the ECUC file does not exist
FOLDER_UNREADABLE = "RDR-FILE-005"  # a folder could not be listed

# ---- parsing
PARSE_MALFORMED = "RDR-PARSE-001"  # not well-formed XML (also: truncated file)
PARSE_EMPTY = "RDR-PARSE-002"  # zero bytes
PARSE_DOCTYPE = "RDR-PARSE-003"  # DOCTYPE present: entities are not expanded
SCHEMA_UNSUPPORTED = "RDR-SCHEMA-001"  # root element is not AUTOSAR 4.x
SCHEMA_MIXED = "RDR-SCHEMA-002"  # the loaded files use more than one schema version

# ---- references
REF_UNRESOLVED = "RDR-REF-001"  # the referenced object is not in the loaded files
REF_DEST_MISMATCH = "RDR-REF-002"  # DEST names another element kind than the target has
REF_WRONG_TYPE = "RDR-REF-003"  # the target exists but cannot be used at this place
REF_RELATIVE_UNRESOLVED = "RDR-REF-004"  # relative reference without a usable reference base

# ---- names and duplicates
DUPLICATE_PATH = "RDR-DUP-001"  # two objects with the same short-name path
SPLIT_ELEMENT = "RDR-DUP-002"  # one splitable element defined over several files (info)
NAME_CASE = "RDR-NAME-001"  # short-name paths that differ only by letter case
NAME_MISSING = "RDR-NAME-002"  # an object without SHORT-NAME was skipped

# ---- unsupported content
VARIATION_POINT = "RDR-VAR-001"  # variation point: unsupported, object treated as always present
KIND_UNKNOWN = "RDR-KIND-001"  # unknown software component kind
CONNECTOR_UNSUPPORTED = "RDR-KIND-002"  # connector kind the model cannot hold (pass-through)
BEHAVIOR_MULTIPLE = "RDR-KIND-003"  # more than one internal behavior in one component

# ---- DaVinci workspace file
DCF_UNREADABLE = "RDR-DCF-001"
DCF_MISSING_ON_DISK = "RDR-DCF-002"  # listed in the .dcf, missing on disk
DCF_NOT_LISTED = "RDR-DCF-003"  # on disk under an ARXML root, not listed in the .dcf
DCF_NOT_LOADED = "RDR-DCF-004"  # listed in the .dcf and on disk, but outside what is loaded

# ---- cache
CACHE_DISABLED = "RDR-CACHE-001"  # cache folder unusable: this load ran without cache
CACHE_ENTRY_CORRUPT = "RDR-CACHE-002"  # one entry was unreadable: deleted and rebuilt
CACHE_RESET = "RDR-CACHE-003"  # the whole cache file was unreadable: deleted and rebuilt

# ---- ECUC
ECUC_EVENT_UNKNOWN = "RDR-ECUC-001"  # task mapping of an event that is not in the model
ECUC_TASK_UNKNOWN = "RDR-ECUC-002"  # task mapping to a task that is not an OS task
ECUC_MAPPING_CONFLICT = "RDR-ECUC-003"  # one event mapped to different tasks per instance

# ---- last resort
INTERNAL = "RDR-INTERNAL-001"  # unexpected error while reading one file (see the log)
