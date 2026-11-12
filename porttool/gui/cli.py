"""Command line of the web interface: ``porttool gui [--repo PATH] [--port N] [--no-browser]``.

The command builds the application service, opens the repo if one is given, starts the
server on 127.0.0.1, prints the address, opens the default browser and blocks until
Ctrl+C. Then it closes the server and the service.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from pathlib import Path
from typing import cast

import click

from porttool.core.interfaces import AppService
from porttool.gui.errors import GuiError
from porttool.gui.server import ServerHandle, open_in_browser, run_forever, serve
from porttool.gui.settings import settings_of

ServiceFactory = Callable[[], AppService]
_import_module = importlib.import_module  # a seam for the tests


def _real_service() -> AppService:
    """``porttool.pipeline.build_service()``, imported late: it pulls in every package."""
    try:
        module = _import_module("porttool.pipeline")
    except ImportError as error:
        raise GuiError(
            "The pipeline package of PortTool cannot be imported, so the web interface "
            "has nothing to show.",
            detail=str(error),
        ) from error
    factory = getattr(module, "build_service", None)
    if not callable(factory):
        raise GuiError("porttool.pipeline has no build_service() function.")
    return cast(AppService, factory())


def make_gui_command(
    service_factory: ServiceFactory | None = None,
    block: Callable[[ServerHandle], None] | None = None,
) -> click.Command:
    """Build the ``gui`` command. Tests pass a fake service factory and a ``block`` function."""

    @click.command(name="gui")
    @click.option(
        "--repo",
        type=click.Path(file_okay=False, path_type=Path),
        default=None,
        help="Folder of the ECU repo to open (it holds porttool.toml).",
    )
    @click.option(
        "--port",
        type=click.IntRange(0, 65535),
        default=None,
        help="Port on 127.0.0.1 (default: [gui] port of porttool.toml, else a free port).",
    )
    @click.option("--no-browser", is_flag=True, help="Only print the address; open no browser.")
    def gui(repo: Path | None, port: int | None, no_browser: bool) -> None:
        """Open the PortTool page in the browser (served on this computer only)."""
        service = (service_factory or _real_service)()
        try:
            if repo is not None:
                for finding in service.open_repo(repo):
                    click.echo(
                        f"{finding.severity.value.upper()} {finding.code}: {finding.message}"
                    )
            chosen = port if port is not None else settings_of(service.config()).port
            handle = serve(service, port=chosen, open_browser=False)
            try:
                click.echo(f"PortTool is running at {handle.url}")
                click.echo("Keep this window open. Press Ctrl+C to stop PortTool.")
                if not no_browser and not open_in_browser(handle.url):
                    click.echo("The browser could not be opened: open the address above by hand.")
                (block or run_forever)(handle)
            finally:
                handle.close()
        finally:
            service.close()
        click.echo("PortTool stopped.")

    return gui


def register_cli(group: click.Group) -> None:
    """Add ``gui`` to the root command group."""
    group.add_command(make_gui_command())
