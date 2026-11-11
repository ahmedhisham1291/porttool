"""Request validator: rules, routing, change set and pending queue.

Public API::

    from porttool.validator import ChangeValidator, validate

    outcome = validate(model, config, rows, pending, now_utc)   # pure function
    outcome = ChangeValidator().validate(model, config, rows, pending, now_utc)

``ChangeValidator`` implements ``porttool.core.interfaces.Validator``. The Finding codes
live in ``porttool.validator.codes``; see HANDOFF.md in this folder for the full table.
"""

from __future__ import annotations

from porttool.validator import codes
from porttool.validator.cli import register_cli
from porttool.validator.codes import ALL_CODES, CODES, describe, format_code_table
from porttool.validator.validator import ChangeValidator, request_id_of, validate

__all__ = [
    "ALL_CODES",
    "CODES",
    "ChangeValidator",
    "codes",
    "describe",
    "format_code_table",
    "register_cli",
    "request_id_of",
    "validate",
]
