"""Fixtures of the discovery tests: the synthetic repo, with and without git."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from porttool.discovery.runner import DiscoveryOptions, DiscoveryResult, run_discovery
from tests.unit.discovery import synth


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return synth.build_repo(tmp_path / "repo")


@pytest.fixture
def git_repo(repo: Path) -> Path:
    synth.git_init(repo)
    return repo


Runner = Callable[..., DiscoveryResult]


@pytest.fixture
def discover() -> Runner:
    """Run discovery without tools unless the test asks for them."""

    def run(repo: Path, **overrides: Any) -> DiscoveryResult:
        overrides.setdefault("no_tools", True)
        return run_discovery(DiscoveryOptions(repo=str(repo), **overrides))

    return run


def all_output_text(folder: str | Path) -> str:
    """Every byte discovery wrote into a report folder, as one string."""
    parts: list[str] = []
    for path in sorted(Path(folder).rglob("*")):
        if path.is_file():
            parts.append(str(path.relative_to(folder)))
            parts.append(path.read_bytes().decode("utf-8", errors="replace"))
    return "\n".join(parts)


def load_json(folder: str | Path, name: str = "discovery.json") -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((Path(folder) / name).read_text(encoding="utf-8"))
    return loaded


def codes(result: DiscoveryResult) -> list[str]:
    return [finding["code"] for finding in result.report["findings"]]
