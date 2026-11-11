"""Fixtures of the workbook tests."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from porttool.core.config import ProjectConfig
from porttool.core.models import Model
from porttool.core.sheets import WorkbookMeta
from porttool.workbook import ExportOptions, XlsxWorkbookIO
from tests.helpers.sample_model import build_sample_config, build_sample_model
from tests.unit.workbook.helpers import make_meta


class SleepRecorder:
    """Stands in for ``time.sleep``: records the waits, never waits."""

    def __init__(self) -> None:
        self.waits: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


@pytest.fixture(scope="session")
def model() -> Model:
    return build_sample_model()


@pytest.fixture(scope="session")
def meta(model: Model) -> WorkbookMeta:
    return make_meta(model)


@pytest.fixture
def config(tmp_path: Path) -> ProjectConfig:
    return build_sample_config(tmp_path / "repo")


@pytest.fixture
def sleeper() -> SleepRecorder:
    return SleepRecorder()


@pytest.fixture
def io(sleeper: SleepRecorder) -> XlsxWorkbookIO:
    return XlsxWorkbookIO(ExportOptions(), sleep=sleeper)


@pytest.fixture(scope="session")
def exported_master(
    tmp_path_factory: pytest.TempPathFactory, model: Model, meta: WorkbookMeta
) -> Path:
    """The sample model exported once per test session. Never edit this file: copy it."""
    folder = tmp_path_factory.mktemp("export")
    target = folder / "requests.xlsx"
    findings = XlsxWorkbookIO().export(model, build_sample_config(folder), target, meta)
    assert findings == ()
    return target


@pytest.fixture
def exported(tmp_path: Path, exported_master: Path) -> Path:
    """A private copy of the exported sample workbook."""
    return Path(shutil.copy(exported_master, tmp_path / "requests.xlsx"))
