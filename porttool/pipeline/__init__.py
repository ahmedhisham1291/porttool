"""Pipeline: the application service, the apply transaction, git, recovery and the report.

Public API::

    from porttool.pipeline import PortToolService, build_service

    service = build_service()            # implements porttool.core.interfaces.AppService
    service.open_repo(repo_root)
    job_id = service.start_load()
"""

from porttool.pipeline.errors import ApplyFailedError, PipelineError, RecoveryNeededError
from porttool.pipeline.service import PortToolService, apply_summary, build_service
from porttool.pipeline.settings import DAVINCI_STEPS, PipelineSettings, read_settings

__all__ = [
    "DAVINCI_STEPS",
    "ApplyFailedError",
    "PipelineError",
    "PipelineSettings",
    "PortToolService",
    "RecoveryNeededError",
    "apply_summary",
    "build_service",
    "read_settings",
]
