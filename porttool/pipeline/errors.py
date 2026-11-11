"""Exceptions of the pipeline. Expected problems are Findings (``codes.py``), not these."""

from __future__ import annotations

from porttool.core.errors import RepoStateError


class PipelineError(RepoStateError):
    """The service refused a call (no repository open, another job running, ...)."""

    default_message = "PortTool cannot do this right now."


class RecoveryNeededError(PipelineError):
    default_message = (
        "An earlier apply did not finish. Roll it back first ('porttool rollback' or the "
        "Rollback button); nothing else is allowed until then."
    )


class ApplyFailedError(PipelineError):
    """An unexpected error stopped the apply. The repository was rolled back first."""

    default_message = "The apply failed and the repository was rolled back."
