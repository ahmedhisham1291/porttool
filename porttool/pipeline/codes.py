"""Finding codes of the pipeline (prefix ``PIP-``). Stable: tests and the GUI match on them."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

# ---- configuration ([pipeline] table)
CFG_TYPE = "PIP-CFG-001"
CFG_UNKNOWN_KEY = "PIP-CFG-002"
CFG_UNKNOWN_STEP = "PIP-CFG-003"

# ---- pending file
PEND_MALFORMED = "PIP-PEND-001"
PEND_CONFLICT = "PIP-PEND-002"
PEND_UNREADABLE = "PIP-PEND-003"

# ---- preflight
PRE_LOCK = "PIP-PRE-001"
PRE_JOURNAL = "PIP-PRE-002"
PRE_NO_DRY_RUN = "PIP-PRE-003"
PRE_MODEL_CHANGED = "PIP-PRE-004"
PRE_STALE = "PIP-PRE-005"
PRE_PLAN_ERRORS = "PIP-PRE-006"
PRE_PENDING = "PIP-PRE-007"
PRE_DELETE = "PIP-PRE-008"
PRE_DISK = "PIP-PRE-009"
PRE_GUI = "PIP-PRE-010"
PRE_GIT_MISSING = "PIP-PRE-011"
PRE_NOT_A_REPO = "PIP-PRE-012"
PRE_NO_COMMIT = "PIP-PRE-013"
PRE_IN_PROGRESS = "PIP-PRE-014"
PRE_DIRTY = "PIP-PRE-015"
PRE_UNTRACKED_WORKSPACE = "PIP-PRE-016"
PRE_UNTRACKED_OTHER = "PIP-PRE-017"
PRE_IDENTITY = "PIP-PRE-018"
PRE_RUNNER = "PIP-PRE-019"

# ---- apply
APPLY_NOTHING = "PIP-APPLY-001"
APPLY_CANCELLED = "PIP-APPLY-002"
APPLY_FAILED = "PIP-APPLY-003"
APPLY_WRITER = "PIP-APPLY-004"
APPLY_NOT_JOURNALED = "PIP-APPLY-005"
APPLY_NOT_APPLIED = "PIP-APPLY-006"
APPLY_STEPS_SKIPPED = "PIP-APPLY-007"
APPLY_LATE_CANCEL = "PIP-APPLY-008"
APPLY_NO_WORKSPACE_WRITE = "PIP-APPLY-009"

# ---- DaVinci steps
DV_CHECK_FAILED = "PIP-DV-001"
DV_FALLBACK_USED = "PIP-DV-002"
DV_FALLBACK_FAILED = "PIP-DV-003"
DV_SCRIPT_FAILED = "PIP-DV-004"
DV_VALIDATE_FAILED = "PIP-DV-005"
DV_GENERATE_FAILED = "PIP-DV-006"
DV_MANUAL_MAPPING = "PIP-DV-007"
DV_GENERATED_IGNORED = "PIP-DV-008"

# ---- verification
VER_MISSING = "PIP-VER-001"
VER_MISMATCH = "PIP-VER-002"
VER_LOST = "PIP-VER-003"
VER_NEW_ERRORS = "PIP-VER-004"
VER_UNJOURNALED = "PIP-VER-005"
VER_STILL_THERE = "PIP-VER-006"

# ---- packages and report
PKG_WRITTEN = "PIP-PKG-001"
PKG_DELTA = "PIP-PKG-002"
REP_UNTRACKED = "PIP-REP-001"

# ---- commit
COMMIT_IDENTITY = "PIP-COMMIT-001"
COMMIT_REJECTED = "PIP-COMMIT-002"
COMMIT_EMPTY = "PIP-COMMIT-003"

# ---- rollback and recovery
RB_DONE = "PIP-RB-001"
RB_BRANCH_KEPT = "PIP-RB-002"
RB_SCOPE_IGNORED = "PIP-RB-003"
RB_NOTHING = "PIP-RB-004"
RB_LEFTOVER = "PIP-RB-005"
RB_FAILED = "PIP-RB-006"
RB_BRANCH_GONE = "PIP-RB-007"
REC_NEEDED = "PIP-REC-001"

# ---- doctor
DOC_GIT = "PIP-DOC-001"
DOC_RUNNER = "PIP-DOC-002"
DOC_OK = "PIP-DOC-003"

_DESCRIPTIONS: dict[str, str] = {
    CFG_TYPE: "A [pipeline] setting has the wrong type; the default is used.",
    CFG_UNKNOWN_KEY: "Unknown key in the [pipeline] table.",
    CFG_UNKNOWN_STEP: "Unknown name in [pipeline] davinci_steps.",
    PEND_MALFORMED: "The pending file is not a valid pending queue.",
    PEND_CONFLICT: "The pending file holds git merge-conflict markers.",
    PEND_UNREADABLE: "The pending file cannot be read.",
    PRE_LOCK: "Another PortTool instance works on this repository.",
    PRE_JOURNAL: "The journal of an unfinished apply exists; roll back first.",
    PRE_NO_DRY_RUN: "No dry run to apply.",
    PRE_MODEL_CHANGED: "The model was reloaded after the dry run.",
    PRE_STALE: "Files changed on disk since the model was loaded.",
    PRE_PLAN_ERRORS: "The dry run reported errors that block the apply.",
    PRE_PENDING: "The pending file changed since the dry run or cannot be used.",
    PRE_DELETE: "Delete requests are refused unless [pipeline] allow_delete = true.",
    PRE_DISK: "Not enough free disk space.",
    PRE_GUI: "DaVinci Developer or Configurator seems to have the project open.",
    PRE_GIT_MISSING: "The git executable was not found on PATH.",
    PRE_NOT_A_REPO: "The repository folder is not inside a git work tree.",
    PRE_NO_COMMIT: "The git repository has no commit yet.",
    PRE_IN_PROGRESS: "A merge, rebase, cherry-pick, revert or bisect is in progress.",
    PRE_DIRTY: "Tracked files have uncommitted changes.",
    PRE_UNTRACKED_WORKSPACE: "Untracked files inside the workspace folders.",
    PRE_UNTRACKED_OTHER: "Untracked files outside the workspace folders (warning).",
    PRE_IDENTITY: "git user.name / user.email is not set.",
    PRE_RUNNER: "The DaVinci runner is not available although a DaVinci step is enabled.",
    APPLY_NOTHING: "Nothing to apply.",
    APPLY_CANCELLED: "The apply was cancelled and rolled back.",
    APPLY_FAILED: "The apply failed and was rolled back.",
    APPLY_WRITER: "The writer reported an error.",
    APPLY_NOT_JOURNALED: "The writer changed a file without journaling it first.",
    APPLY_NOT_APPLIED: "The writer did not apply a planned operation.",
    APPLY_STEPS_SKIPPED: "DaVinci steps were skipped; how to enable them.",
    APPLY_LATE_CANCEL: "Cancel came after the commit; the apply is complete.",
    APPLY_NO_WORKSPACE_WRITE: "No request is applied to the workspace now.",
    DV_CHECK_FAILED: "The workspace check failed.",
    DV_FALLBACK_USED: "Direct edit failed the check; the fallback import was used.",
    DV_FALLBACK_FAILED: "The fallback import failed too.",
    DV_SCRIPT_FAILED: "The script task (task mapping, solving actions) failed.",
    DV_VALIDATE_FAILED: "Project validation failed.",
    DV_GENERATE_FAILED: "Generation failed.",
    DV_MANUAL_MAPPING: "New triggers need a manual task mapping.",
    DV_GENERATED_IGNORED: "The generated folder is ignored by git.",
    VER_MISSING: "An applied object is missing after the reload.",
    VER_MISMATCH: "An applied object differs from the request after the reload.",
    VER_LOST: "Objects that existed before the apply are gone.",
    VER_NEW_ERRORS: "The reloaded workspace has new errors.",
    VER_UNJOURNALED: "Files changed that are not in the journal.",
    VER_STILL_THERE: "A deleted object still exists after the reload.",
    PKG_WRITTEN: "A request package for a model owner was written.",
    PKG_DELTA: "The delta ARXML of a request package could not be built.",
    REP_UNTRACKED: "The report folder is not ignored by git.",
    COMMIT_IDENTITY: "git refused the commit: user.name / user.email is not set.",
    COMMIT_REJECTED: "git refused the commit (hook or other reason).",
    COMMIT_EMPTY: "Nothing changed on disk; no commit was made.",
    RB_DONE: "The repository was restored.",
    RB_BRANCH_KEPT: "The branch of the interrupted apply holds a commit and was kept.",
    RB_SCOPE_IGNORED: "A folder written by DaVinci tools is ignored by git; not restored.",
    RB_NOTHING: "Nothing to roll back.",
    RB_LEFTOVER: "Journaled paths still differ from the original commit.",
    RB_FAILED: "The rollback could not finish; the journal stays open.",
    RB_BRANCH_GONE: "The original branch no longer exists; HEAD is detached.",
    REC_NEEDED: "An earlier apply did not finish; only rollback is allowed.",
    DOC_GIT: "git problem found by doctor.",
    DOC_RUNNER: "The DaVinci runner is not available.",
    DOC_OK: "doctor information.",
}

DESCRIPTIONS: Mapping[str, str] = MappingProxyType(_DESCRIPTIONS)
