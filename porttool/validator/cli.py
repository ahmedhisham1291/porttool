"""Command-line wiring of the validator package (click).

The validator itself is a pure function that needs a loaded model, so the dry-run
command belongs to the pipeline package. This module only adds ``validator-codes``,
which prints the table of Finding codes.
"""

from __future__ import annotations

import click

from porttool.validator.codes import format_code_table


@click.command("validator-codes", help="List the Finding codes of the request validator.")
def validator_codes() -> None:
    click.echo(format_code_table())


def register_cli(app: click.Group) -> None:
    """Add the validator commands to the PortTool command-line application."""
    app.add_command(validator_codes)
