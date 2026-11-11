"""`porttool discover`: self-service study of the real ECU repo on the owner's machine.

Public API (see HANDOFF.md):

* ``register_cli(group)``: adds the ``discover`` command group to a click group.
* ``run_discovery(options, progress)``: gathers the facts and writes the report folder.
* ``run_golden_diff(options, progress)``: compares two commits and writes the report.
"""

from porttool.discovery.cli import register_cli
from porttool.discovery.runner import (
    DiscoveryOptions,
    DiscoveryResult,
    GoldenDiffOptions,
    run_discovery,
    run_golden_diff,
)

__all__ = [
    "DiscoveryOptions",
    "DiscoveryResult",
    "GoldenDiffOptions",
    "register_cli",
    "run_discovery",
    "run_golden_diff",
]
