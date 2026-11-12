"""Fixtures of the pipeline tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.unit.pipeline.support import Harness, Repo, make_repo, make_service


@pytest.fixture(autouse=True)
def _local_app_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cache, locks and journals default to %LOCALAPPDATA%: keep them inside the test folder."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    return make_repo(tmp_path / "repo", tmp_path / "home")


@pytest.fixture
def harness(repo: Repo, tmp_path: Path) -> Iterator[Harness]:
    made = make_service(repo, tmp_path)
    yield made
    made.service.close()
