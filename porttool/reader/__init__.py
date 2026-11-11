"""ARXML and ECUC loading: turns an ECU repository into ``porttool.core.models.Model``.

Public API (see HANDOFF.md):

* ``ArxmlReader`` implements ``porttool.core.interfaces.Reader``.
* ``ReaderOptions`` and ``LoadStats`` configure it and describe the last load.
* ``load_blocking`` runs a load without the engine (command line, scripts).
* ``register_cli`` adds the ``load`` and ``list`` commands to a click group.
* ``porttool.reader.codes`` holds the stable ``RDR-`` finding codes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from porttool.reader import codes
from porttool.reader.ecuc import EcucLayout
from porttool.reader.reader import ArxmlReader, LoadStats, ReaderOptions
from porttool.reader.standalone import PoolJobContext, load_blocking

if TYPE_CHECKING:
    import click

__all__ = [
    "ArxmlReader",
    "EcucLayout",
    "LoadStats",
    "PoolJobContext",
    "ReaderOptions",
    "codes",
    "load_blocking",
    "register_cli",
]


def register_cli(group: click.Group) -> None:
    """Add ``load`` and ``list`` to ``group``. click is imported only when this is called."""
    from porttool.reader.cli import register_cli as _register

    _register(group)
