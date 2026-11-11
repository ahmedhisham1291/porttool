"""Deterministic generator of a synthetic AUTOSAR 4.x repo for tests and benchmarks.

::

    from tests.fixtures.generator import LARGE, SMALL, generate

    truth = generate(tmp_path / "repo", SMALL)      # writes the repo, returns GroundTruth
    truth.counts["ports"], truth.port_paths, truth.unconnected, truth.to_model()

Command line: ``python3 -m tests.fixtures.generator <folder> --preset large``.
"""

from tests.fixtures.generator.generate import generate, render
from tests.fixtures.generator.spec import LARGE, PRESETS, SMALL, FileLayout, GeneratorSpec
from tests.fixtures.generator.truth import GroundTruth

__all__ = [
    "LARGE",
    "PRESETS",
    "SMALL",
    "FileLayout",
    "GeneratorSpec",
    "GroundTruth",
    "generate",
    "render",
]
