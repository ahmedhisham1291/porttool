"""Exception hierarchy. CONTRACT FILE.

Expected problems (a rejected request, a dangling reference, a locked file) are NOT
exceptions: they are ``Finding`` objects, see ``porttool.core.results``.
Exceptions are for unexpected failures. Each carries a stable code and a user message.
"""

from __future__ import annotations


class PortToolError(Exception):
    """Base class of every error PortTool raises on purpose."""

    code: str = "PT-E000"
    default_message: str = "Unexpected PortTool error."

    def __init__(self, message: str | None = None, *, detail: str | None = None) -> None:
        self.user_message: str = message or self.default_message
        self.detail: str | None = detail
        super().__init__(self.user_message)

    def __str__(self) -> str:
        return f"[{self.code}] {self.user_message}"


class ConfigError(PortToolError):
    code = "PT-E100"
    default_message = "The PortTool configuration is missing or invalid."


class ArxmlParseError(PortToolError):
    code = "PT-E200"
    default_message = "An ARXML file could not be read."


class WorkbookError(PortToolError):
    code = "PT-E300"
    default_message = "The Excel workbook could not be read or written."


class WriteError(PortToolError):
    code = "PT-E400"
    default_message = "A file could not be written."


class RepoStateError(PortToolError):
    code = "PT-E410"
    default_message = "The repository is not in the state PortTool expected."


class GitError(PortToolError):
    code = "PT-E500"
    default_message = "A git command failed."


class ToolRunError(PortToolError):
    code = "PT-E600"
    default_message = "A DaVinci command-line tool failed."


class ToolTimeoutError(ToolRunError):
    code = "PT-E610"
    default_message = "A DaVinci command-line tool did not finish in time."


class LicenseError(ToolRunError):
    code = "PT-E620"
    default_message = "A DaVinci license is missing or could not be reached."


class JobCancelled(PortToolError):  # noqa: N818 - reads better than JobCancelledError
    """Raised by ``JobContext.check_cancelled()`` inside worker code."""

    code = "PT-E900"
    default_message = "The job was cancelled."
