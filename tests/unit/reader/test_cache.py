"""The record cache: warm loads, invalidation, damage, and a cache that cannot be used."""

from __future__ import annotations

import pickle
import sqlite3
import zlib
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from porttool.core.models import Model
from porttool.core.results import Severity
from porttool.reader import ArxmlReader, EcucLayout, ReaderOptions, cache, codes, parse
from porttool.reader.cache import DB_NAME, CacheUnavailable, RecordCache
from porttool.reader.records import CACHE_FORMAT, empty_records
from tests.unit.reader import samples
from tests.unit.reader.conftest import Repo, codes_of, findings_with
from tests.unit.reader.samples import CT, doc, package, swc

FILE_COUNT = len(samples.SAMPLE_FILES) + 1  # plus the ECUC file


@pytest.fixture
def sample(repo: Repo) -> Repo:
    samples.write_sample_repo(repo.root)
    return repo


def load(repo: Repo, **overrides: Any) -> Model:
    return repo.load(ecuc_file=repo.path(samples.F_ECUC), **overrides)


def parsed_and_cached(repo: Repo) -> tuple[int, int]:
    stats = repo.reader.last_stats
    assert stats is not None
    return stats.files_parsed, stats.files_from_cache


def same_content(a: Model, b: Model) -> bool:
    return all(
        getattr(a, name) == getattr(b, name)
        for name in (
            "components",
            "ports",
            "interfaces",
            "runnables",
            "access_points",
            "triggers",
            "task_mappings",
            "tasks",
            "compositions",
            "prototypes",
            "connectors",
            "data_types",
            "base_types",
            "compu_methods",
            "constraints",
            "units",
            "mapping_sets",
            "constants",
            "files",
            "findings",
        )
    )


def test_warm_load_parses_nothing_and_gives_the_same_model(
    sample: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    cold = load(sample)
    assert parsed_and_cached(sample) == (FILE_COUNT, 0)
    assert (sample.cache_dir / DB_NAME).is_file()

    def must_not_parse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a warm load must not parse any file")

    monkeypatch.setattr(parse, "_read_bytes", must_not_parse)
    monkeypatch.setattr(parse, "_extract_stream", must_not_parse)
    warm = load(sample)
    assert parsed_and_cached(sample) == (0, FILE_COUNT)
    assert same_content(cold, warm) and warm.model_hash == cold.model_hash
    # a new reader object (a new program start) uses the same cache
    assert same_content(
        cold, sample.load(reader=ArxmlReader(), ecuc_file=sample.path(samples.F_ECUC))
    )


def test_load_after_one_file_change_parses_only_that_file(sample: Repo) -> None:
    before = load(sample)
    sample.write(samples.F_SWC_M, doc(package("ComponentTypes", swc("SwcM"), swc("SwcNew"))))
    after = load(sample)
    assert parsed_and_cached(sample) == (1, FILE_COUNT - 1)
    assert f"{CT}/SwcNew" in after.components and f"{CT}/SwcNew" not in before.components
    assert after.model_hash != before.model_hash
    load(sample)
    assert parsed_and_cached(sample) == (0, FILE_COUNT)


def test_deleted_and_added_files_update_the_cache(sample: Repo) -> None:
    load(sample)
    sample.path(samples.F_OTHER).unlink()
    sample.write("Config/Developer/Added.arxml", doc(package("ComponentTypes", swc("Added"))))
    model = load(sample)
    assert parsed_and_cached(sample) == (1, FILE_COUNT - 1)
    assert f"{CT}/NvM" not in model.components and f"{CT}/Added" in model.components
    with closing(sqlite3.connect(sample.cache_dir / DB_NAME)) as connection:
        rows = {row[0] for row in connection.execute("SELECT rel_path FROM entries")}
    assert samples.F_OTHER not in rows and "Config/Developer/Added.arxml" in rows
    assert len(rows) == FILE_COUNT


def test_failed_parse_is_cached_with_its_finding(sample: Repo) -> None:
    sample.write("Config/Developer/Broken.arxml", "<AUTOSAR")
    first = load(sample)
    second = load(sample)
    assert parsed_and_cached(sample) == (0, FILE_COUNT + 1)
    assert codes_of(first) == codes_of(second) == [codes.PARSE_MALFORMED]


def set_entry(repo: Repo, rel_path: str, data: bytes, *, fix_crc: bool) -> None:
    with closing(sqlite3.connect(repo.cache_dir / DB_NAME)) as connection:
        if fix_crc:
            connection.execute(
                "UPDATE entries SET data = ?, crc = ? WHERE rel_path = ?",
                (data, zlib.crc32(data), rel_path),
            )
        else:
            connection.execute("UPDATE entries SET data = ? WHERE rel_path = ?", (data, rel_path))
        connection.commit()


@pytest.mark.parametrize(
    ("label", "data", "fix_crc"),
    [
        ("bit rot: checksum mismatch", b"\x80\x05garbage", False),
        ("truncated pickle", b"\x80\x05\x95", True),
        ("not a pickle", b"hello", True),
        ("wrong shape", pickle.dumps(("only", "two")), True),
        (
            "old format number",
            pickle.dumps(tuple(empty_records("x", 1, 1, "ok")._replace(format=0))),
            True,
        ),
        (
            "entry of another file",
            pickle.dumps(tuple(empty_records("other.arxml", 1, 1, "ok"))),
            True,
        ),
    ],
)
def test_corrupt_cache_entry_is_deleted_and_rebuilt(
    sample: Repo, label: str, data: bytes, fix_crc: bool
) -> None:
    cold = load(sample)
    set_entry(sample, samples.F_SWC_A, data, fix_crc=fix_crc)
    model = load(sample)
    assert parsed_and_cached(sample) == (1, FILE_COUNT - 1), label
    corrupt = findings_with(model, codes.CACHE_ENTRY_CORRUPT)
    assert [(f.file, f.severity) for f in corrupt] == [(samples.F_SWC_A, Severity.INFO)]
    assert model.components == cold.components and model.ports == cold.ports
    # the entry was rebuilt: the next load is clean and warm
    healed = load(sample)
    assert parsed_and_cached(sample) == (0, FILE_COUNT)
    assert healed.findings == ()


def test_cache_entry_cannot_run_code(sample: Repo, tmp_path: Path) -> None:
    load(sample)
    marker = tmp_path / "executed.txt"

    class Evil:
        def __reduce__(self) -> tuple[Any, tuple[Any, ...]]:
            return (Path.write_text, (marker, "boom"))

    set_entry(sample, samples.F_SWC_A, pickle.dumps(Evil()), fix_crc=True)
    model = load(sample)
    assert not marker.exists()
    assert [f.file for f in findings_with(model, codes.CACHE_ENTRY_CORRUPT)] == [samples.F_SWC_A]
    with pytest.raises(ValueError, match="pathlib"):
        cache.decode(pickle.dumps(Evil()))


@pytest.mark.parametrize("garbage", [b"this is not a database, " * 200, b""])
def test_corrupt_cache_file_is_deleted_and_rebuilt(sample: Repo, garbage: bytes) -> None:
    cold = load(sample)
    (sample.cache_dir / DB_NAME).write_bytes(garbage)
    model = load(sample)
    assert parsed_and_cached(sample) == (FILE_COUNT, 0)
    assert model.components == cold.components
    if garbage:
        assert codes_of(model) == [codes.CACHE_RESET]
        assert model.findings[0].severity is Severity.INFO
    load(sample)
    assert parsed_and_cached(sample) == (0, FILE_COUNT)


def test_cache_folder_not_writable_disables_the_cache(sample: Repo, tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where the cache folder should be")
    model = load(sample, cache_dir=blocker / "cache")
    disabled = findings_with(model, codes.CACHE_DISABLED)
    assert len(disabled) == 1 and disabled[0].severity is Severity.WARNING
    assert str(blocker / "cache") in disabled[0].message
    assert len(model.components) == 12  # the load itself is complete
    stats = sample.reader.last_stats
    assert stats is not None and stats.cache_enabled is False and stats.files_parsed == FILE_COUNT
    load(sample, cache_dir=blocker / "cache")
    assert parsed_and_cached(sample) == (FILE_COUNT, 0)  # still no cache, still works


def test_cache_that_cannot_be_written_is_a_finding(
    sample: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    def read_only(self: RecordCache, records: Any, delete: Any = ()) -> None:
        raise CacheUnavailable("attempt to write a readonly database")

    monkeypatch.setattr(RecordCache, "write", read_only)
    model = load(sample)
    assert codes_of(model) == [codes.CACHE_DISABLED]
    assert "readonly" in model.findings[0].message
    assert len(model.components) == 12


def test_sqlite_write_error_becomes_cache_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RecordCache(tmp_path / "cache", "v1")
    store.write([empty_records("a.arxml", 1, 2, "ok")])
    assert set(store.read({"a.arxml": (1, 2)}).hits) == {"a.arxml"}

    def locked(*args: Any, **kwargs: Any) -> Any:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(sqlite3, "connect", locked)
    with pytest.raises(CacheUnavailable, match="locked"):
        store.write([empty_records("b.arxml", 1, 2, "ok")])
    with pytest.raises(CacheUnavailable, match="locked"):
        store.read({"a.arxml": (1, 2)})


def test_cache_version_change_drops_every_entry(sample: Repo) -> None:
    load(sample)
    other_layout = ReaderOptions(ecuc_layout=EcucLayout(os_task="/Os/OsTaskRenamed"))
    model = sample.load(reader=ArxmlReader(other_layout), ecuc_file=sample.path(samples.F_ECUC))
    # the extraction keeps other ECUC containers now: nothing cached may be reused
    assert model.tasks == frozenset()
    store = RecordCache(sample.cache_dir, cache.cache_version(EcucLayout().signature))
    assert store.read({}).hits == {}
    load(sample)
    assert parsed_and_cached(sample) == (FILE_COUNT, 0)
    assert str(CACHE_FORMAT) == cache.cache_version("x").split("|")[0]


def test_cache_can_be_switched_off(sample: Repo) -> None:
    reader = ArxmlReader(ReaderOptions(use_cache=False))
    for _ in range(2):
        sample.load(reader=reader, ecuc_file=sample.path(samples.F_ECUC))
        assert reader.last_stats is not None
        assert (reader.last_stats.files_parsed, reader.last_stats.cache_enabled) == (
            FILE_COUNT,
            False,
        )
    assert not sample.cache_dir.exists()


def test_default_cache_folder(
    sample: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The path logic of the default; that %LOCALAPPDATA% is set is a Windows fact."""
    local = tmp_path / "LocalAppData"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    expected = cache.default_cache_dir(sample.root)
    assert expected is not None
    assert expected.parent == local / "PortTool" / "cache"
    assert expected.name.startswith("repo-") and len(expected.name) == len("repo-") + 16
    assert cache.default_cache_dir(sample.root) == expected  # stable
    assert cache.default_cache_dir(tmp_path / "another") != expected
    model = load(sample, cache_dir=None)
    assert model.findings == () and (expected / DB_NAME).is_file()
    load(sample, cache_dir=None)
    assert parsed_and_cached(sample) == (0, FILE_COUNT)


def test_no_cache_folder_and_no_localappdata_is_a_finding(
    sample: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert cache.default_cache_dir(sample.root) is None
    model = load(sample, cache_dir=None)
    assert codes_of(model) == [codes.CACHE_DISABLED]
    assert len(model.components) == 12


def test_cache_is_written_in_batches_during_a_long_load(
    repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cancelled cold load keeps what it had parsed before the cancel."""
    from porttool.core.errors import JobCancelled
    from porttool.reader import reader as reader_module

    for index in range(40):
        repo.write(f"Config/Developer/S{index:02d}.arxml", doc(package("P", swc(f"S{index:02d}"))))
    monkeypatch.setattr(reader_module, "_FLUSH_COUNT", 8)
    monkeypatch.setattr(reader_module, "_PARSE_WINDOW", 4)
    ctx = repo.context(workers=1, cancel_after_progress=30)
    with pytest.raises(JobCancelled):
        repo.load(ctx=ctx)
    monkeypatch.undo()
    model = repo.load()
    stats = repo.reader.last_stats
    assert stats is not None and len(model.components) == 40
    assert stats.files_from_cache >= 16 and stats.files_parsed <= 24
