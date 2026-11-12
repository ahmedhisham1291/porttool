"""In-place ARXML edits and delta ARXML. See HANDOFF.md.

* ``ArxmlWriter`` implements ``porttool.core.interfaces.Writer``.
* ``WriterSettings`` is the ``[writer]`` table of porttool.toml.
* ``porttool.writer.codes`` holds the stable ``WRT-`` finding codes.
* ``created_events`` reads the ``WRT-INFO-EVENT`` findings of a ``WriteOutcome``.
* ``register_cli`` adds the ``writer-plan``, ``writer-delta`` and ``writer-codes`` commands.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from porttool.writer import codes
from porttool.writer.events import CreatedEvent, created_events
from porttool.writer.settings import WriterSettings
from porttool.writer.writer import ArxmlWriter

if TYPE_CHECKING:
    import click

__all__ = [
    "ArxmlWriter",
    "CreatedEvent",
    "WriterSettings",
    "codes",
    "created_events",
    "register_cli",
]


def register_cli(group: click.Group) -> None:
    """Add the writer commands to ``group``. click is imported only when this is called."""
    from porttool.writer.cli import register_cli as _register

    _register(group)
