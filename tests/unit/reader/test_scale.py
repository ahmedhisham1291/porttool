"""The scale generator at a reduced size: counts, warm load and one-file change.

The full-size measurement is ``python3 -m tests.unit.reader.bench_scale`` (see HANDOFF.md).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from porttool.reader import load_blocking
from tests.unit.reader.scale_generator import generate


@pytest.mark.slow
def test_scaled_down_repository_loads_completely(tmp_path: Path) -> None:
    scale = generate(
        tmp_path / "repo",
        swcs=30,
        provide_ports=500,
        interfaces=200,
        data_types=300,
        interface_files=100,
        data_type_files=60,
        filler_files=9,
        target_bytes=12_000_000,
        cache_dir=tmp_path / "cache",
    )
    assert scale.files == 30 + 100 + 60 + 1 + 1 + 9 + 1
    assert 9_000_000 < scale.total_bytes < 16_000_000

    model, cold = load_blocking(scale.config, workers=2)
    assert [(f.code, f.message) for f in model.findings] == []
    assert len(model.components) == scale.swcs + 1
    assert len(model.ports) == scale.ports == 2000
    assert len(model.connectors) == scale.connectors == 1500
    assert len(model.interfaces) == scale.interfaces
    assert len(model.runnables) == scale.runnables
    assert len(model.access_points) == scale.access_points == 2000
    assert len(model.triggers) == scale.triggers
    assert len(model.data_types) == scale.data_types
    assert len(model.compu_methods) == 500 and len(model.base_types) == 1
    assert len(model.prototypes) == scale.swcs
    assert len(model.tasks) == scale.tasks
    assert len(model.task_mappings) == scale.task_mappings
    assert model.unconnected() == ()
    assert (cold.files_parsed, cold.files_from_cache) == (scale.files, 0)
    assert cold.bytes_total == scale.total_bytes

    warm_model, warm = load_blocking(scale.config, workers=2)
    assert (warm.files_parsed, warm.files_from_cache) == (0, scale.files)
    assert warm_model.ports == model.ports and warm_model.connectors == model.connectors
    assert warm.seconds_total < cold.seconds_total

    scale.one_swc_file.write_bytes(scale.one_swc_file.read_bytes() + b"\n")
    changed_model, changed = load_blocking(scale.config, workers=2)
    assert (changed.files_parsed, changed.files_from_cache) == (1, scale.files - 1)
    assert changed_model.model_hash != model.model_hash
