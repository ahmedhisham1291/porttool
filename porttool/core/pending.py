"""Pending queue: requests waiting for a model owner or held for a missing port. CONTRACT FILE.

Stored as ``porttool/pending.json`` inside the ECU repo so every team sees it.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from porttool.core.operations import AnyOperation


class PendingState(StrEnum):
    WAITING_FOR_MODEL_OWNER = "waiting for model owner"
    ON_HOLD = "on hold"


class PendingEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    operation: AnyOperation
    state: PendingState
    created_utc: str
    owner_team: str | None = None  # model owner the request was sent to
    depends_on: tuple[str, ...] = ()  # AUTOSAR paths that must exist before release
    note: str = ""


class PendingQueue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    format_version: int = 1
    entries: tuple[PendingEntry, ...] = ()

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)

    @classmethod
    def from_json(cls, text: str) -> PendingQueue:
        return cls.model_validate_json(text)
