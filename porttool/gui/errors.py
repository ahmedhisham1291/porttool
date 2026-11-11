"""Errors of the web interface."""

from __future__ import annotations

from porttool.core.errors import PortToolError


class GuiError(PortToolError):
    """The local web server could not be started or used."""

    code = "PT-E700"
    default_message = "The PortTool web interface could not be started."


class ApiError(Exception):
    """An expected problem of one API call: becomes ``{"error": {"code", "message"}}``."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.status = status
        self.code = code
        self.message = message
