"""Measure the reader on the large synthetic repository. Throw-away tool, not a test.

    python3 -m tests.unit.reader.bench_scale --out /tmp/porttool-scale [--target-mb 500]
        [--workers N] [--profile]

Prints cold load, warm load, load after a one-file change and the peak resident memory
of this process (sampled with psutil). ``--profile`` also runs the cold path once on one
thread under cProfile to show where the Python-side time goes.
"""

from __future__ import annotations

import argparse
import cProfile
import io
import pstats
import shutil
import threading
import time
from pathlib import Path

import psutil

from porttool.reader import EcucLayout, LoadStats, load_blocking, parse
from porttool.reader.build import build_model
from porttool.reader.discovery import discover
from porttool.reader.standalone import default_workers
from tests.unit.reader.scale_generator import ScaleRepo, generate


class PeakMemory:
    """Samples the resident set size of this process every 20 ms."""

    def __init__(self) -> None:
        self._process = psutil.Process()
        self._stop = threading.Event()
        self.peak = self._process.memory_info().rss
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.wait(0.02):
            self.peak = max(self.peak, self._process.memory_info().rss)

    def __enter__(self) -> PeakMemory:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join()
        self.peak = max(self.peak, self._process.memory_info().rss)


def show(label: str, stats: LoadStats, seconds: float, peak: int) -> None:
    megabytes = stats.bytes_parsed / (1024 * 1024)
    print(
        f"{label:22} {seconds:7.2f} s  parsed {stats.files_parsed:5} files ({megabytes:7.1f} MB), "
        f"cached {stats.files_from_cache:5}  peak RSS {peak / (1024 * 1024):7.0f} MB\n"
        f"{'':22} scan {stats.seconds_scan:.2f}  cache read {stats.seconds_cache_read:.2f}  "
        f"parse {stats.seconds_parse:.2f}  cache write {stats.seconds_cache_write:.2f}  "
        f"build {stats.seconds_build:.2f}"
    )


def measure(label: str, scale: ScaleRepo, workers: int) -> LoadStats:
    with PeakMemory() as memory:
        started = time.perf_counter()
        model, stats = load_blocking(scale.config, workers=workers)
        seconds = time.perf_counter() - started
    show(label, stats, seconds, memory.peak)
    errors = [f for f in model.findings]
    if errors:
        print(f"{'':22} {len(errors)} findings, first: {errors[0].code} {errors[0].message}")
    counts = (len(model.components), len(model.ports), len(model.connectors))
    expected = (scale.swcs + 1, scale.ports, scale.connectors)
    if counts != expected:
        raise SystemExit(f"model counts {counts} differ from the generator's {expected}")
    return stats


def profile_single_thread(scale: ScaleRepo) -> None:
    """The cold path on one thread: where does the time go, GIL-bound or not?"""
    suffixes = EcucLayout().container_suffixes
    found = discover(scale.config)
    profiler = cProfile.Profile()
    started = time.perf_counter()
    profiler.enable()
    records = {
        f.rel_path: parse.parse_file(
            f.abs_path, f.rel_path, scale.config.large_file_bytes, suffixes, lambda: None
        )
        for f in found.files
    }
    parsed = time.perf_counter()
    build_model(scale.config, found.files, records)
    profiler.disable()
    finished = time.perf_counter()
    stream = io.StringIO()
    statistics = pstats.Stats(profiler, stream=stream)
    statistics.sort_stats("tottime").print_stats(14)
    print(
        f"\nsingle thread under cProfile: parse+extract {parsed - started:.2f} s, "
        f"build {finished - parsed:.2f} s"
    )
    print("\n".join(stream.getvalue().splitlines()[4:26]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="folder for the repository")
    parser.add_argument("--target-mb", type=int, default=500, help="total size to pad to")
    parser.add_argument("--workers", type=int, default=default_workers())
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--keep", action="store_true", help="do not delete the repository")
    arguments = parser.parse_args()

    repo = arguments.out / "repo"
    cache = arguments.out / "cache"
    shutil.rmtree(arguments.out, ignore_errors=True)
    started = time.perf_counter()
    scale = generate(repo, target_bytes=arguments.target_mb * 1024 * 1024, cache_dir=cache)
    print(
        f"generated {scale.files} files, {scale.total_bytes / (1024 * 1024):.1f} MB, "
        f"{scale.swcs} SWCs, {scale.ports} ports, {scale.connectors} connectors, "
        f"{scale.interfaces} interfaces, {scale.access_points} access points "
        f"in {time.perf_counter() - started:.1f} s; {arguments.workers} workers, "
        f"{psutil.cpu_count()} CPU cores"
    )
    measure("cold load", scale, arguments.workers)
    measure("warm load", scale, arguments.workers)
    scale.one_swc_file.write_bytes(scale.one_swc_file.read_bytes() + b"\n")
    measure("one file changed", scale, arguments.workers)
    if arguments.profile:
        profile_single_thread(scale)
    if not arguments.keep:
        shutil.rmtree(arguments.out, ignore_errors=True)


if __name__ == "__main__":
    main()
