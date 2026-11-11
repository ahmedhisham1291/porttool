"""Cross-check: the reader (Agent 2) against the fixture generator (Agent 1).

The two were written independently. If the reader loads exactly what the generator
says it wrote, both agree on the AUTOSAR XML.
"""

from __future__ import annotations

import dataclasses
import time
from pathlib import Path

import pytest

from porttool.core.config import ProjectConfig
from porttool.core.config_loader import load_config
from porttool.core.interfaces import JobContext
from porttool.core.models import Model
from porttool.engine.engine import Engine
from porttool.reader import ArxmlReader
from tests.fixtures.generator import LARGE, SMALL, GeneratorSpec, GroundTruth, generate


def _load(repo: Path, cache_dir: Path) -> tuple[Model, ProjectConfig, float]:
    loaded = load_config(repo)
    assert not loaded.has_errors, loaded.findings
    config = dataclasses.replace(loaded.config, cache_dir=cache_dir)
    reader = ArxmlReader()

    async def job(ctx: JobContext) -> Model:
        return await reader.load(config, ctx)

    engine = Engine()
    engine.start()
    try:
        started = time.perf_counter()
        model = engine.run_sync(job, name="load")
        elapsed = time.perf_counter() - started
    finally:
        engine.shutdown()
    return model, config, elapsed


def _check(model: Model, truth: GroundTruth) -> None:
    assert set(model.components) == {c.path for c in truth.components}
    assert set(model.ports) == {p.path for p in truth.ports}
    assert set(model.interfaces) == {i.path for i in truth.interfaces}
    assert set(model.runnables) == {r.path for r in truth.runnables}
    assert set(model.access_points) == {a.path for a in truth.access_points}
    assert set(model.triggers) == {t.path for t in truth.triggers}
    assert set(model.prototypes) == {p.path for p in truth.prototypes}
    assert set(model.connectors) == {c.path for c in truth.connectors}
    assert set(model.data_types) == {d.path for d in truth.data_types}
    assert model.tasks == frozenset(truth.tasks)
    assert set(model.task_mappings) == {m.event_path for m in truth.task_mappings}
    unconnected = sorted(i.key for i in model.unconnected())
    assert unconnected == sorted(truth.unconnected)

    for port in truth.ports:
        got = model.ports[port.path]
        assert (got.direction, got.interface_path, got.swc_path) == (
            port.direction,
            port.interface_path,
            port.swc_path,
        )
    for access in truth.access_points:
        got_access = model.access_points[access.path]
        assert (got_access.kind, got_access.port_path, got_access.target) == (
            access.kind,
            access.port_path,
            access.target,
        )
    for trigger in truth.triggers:
        got_trigger = model.triggers[trigger.path]
        assert (got_trigger.kind, got_trigger.runnable_path, got_trigger.port_path) == (
            trigger.kind,
            trigger.runnable_path,
            trigger.port_path,
        )
    for connector in truth.connectors:
        got_connector = model.connectors[connector.path]
        assert got_connector.provider == connector.provider
        assert got_connector.requester == connector.requester
    for mapping in truth.task_mappings:
        assert model.task_mappings[mapping.event_path].task == mapping.task
    owners = {c.name: c.owner for c in model.components.values()}
    for name in truth.model_owned_swcs:
        assert owners[name].value == "model", name


@pytest.mark.parametrize("spec", [SMALL], ids=["small"])
def test_reader_matches_generator_small(tmp_path: Path, spec: GeneratorSpec) -> None:
    truth = generate(tmp_path / "repo", spec)
    model, _, _ = _load(tmp_path / "repo", tmp_path / "cache")
    _check(model, truth)


@pytest.mark.slow
def test_reader_matches_generator_large_and_warm_load_is_fast(tmp_path: Path) -> None:
    truth = generate(tmp_path / "repo", LARGE)
    model, _, cold = _load(tmp_path / "repo", tmp_path / "cache")
    _check(model, truth)
    warm_model, _, warm = _load(tmp_path / "repo", tmp_path / "cache")
    assert warm_model.model_hash == model.model_hash
    print(
        f"\n[integration] LARGE: cold load {cold:.2f} s, warm load {warm:.2f} s, "
        f"{len(model.ports)} ports, {len(model.connectors)} connectors"
    )
    assert cold < 30.0
    assert warm < 3.0
