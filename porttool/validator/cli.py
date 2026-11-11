"""Command-line wiring of the validator package.

The validator itself is a pure function that needs a loaded model, so the dry-run
command belongs to the pipeline package. This module only adds ``validator-codes``,
which prints the table of Finding codes.

Typer is not imported here: ``register_cli`` needs nothing but the ``command``
decorator of the application object, so it also works with any object that offers one.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from porttool.validator.codes import format_code_table


class CommandApp(Protocol):
    """The part of ``typer.Typer`` that ``register_cli`` uses."""

    def command(
        self, name: str | None = None, *, help: str | None = None
    ) -> Callable[[Callable[[], None]], Callable[[], None]]: ...


def print_codes() -> None:
    """Print every validator Finding code with its severity and meaning."""
    print(format_code_table())


def register_cli(app: CommandApp) -> None:
    """Add the validator commands to the PortTool command-line application."""
    app.command("validator-codes", help="List the Finding codes of the request validator.")(
        print_codes
    )
