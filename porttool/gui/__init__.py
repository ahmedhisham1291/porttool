"""Local web interface of PortTool: an HTTP server on 127.0.0.1 plus a plain HTML page.

Public API::

    from porttool.gui import serve, run_forever

    handle = serve(service)          # returns at once; the browser opens handle.url
    run_forever(handle)              # blocks until Ctrl+C, then closes the server

``service`` is any ``porttool.core.interfaces.AppService``. See HANDOFF.md for the
endpoints and the ``[gui]`` settings.
"""

from __future__ import annotations

from porttool.gui.errors import GuiError
from porttool.gui.server import TOKEN_HEADER, ServerHandle, open_in_browser, run_forever, serve
from porttool.gui.settings import GuiSettings

__all__ = [
    "TOKEN_HEADER",
    "GuiError",
    "GuiSettings",
    "ServerHandle",
    "open_in_browser",
    "run_forever",
    "serve",
]
