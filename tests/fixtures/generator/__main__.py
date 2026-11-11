"""``python3 -m tests.fixtures.generator <folder> [--preset small|large] [--seed N]``."""

from __future__ import annotations

import argparse
import dataclasses
import time

from tests.fixtures.generator import PRESETS, generate


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a synthetic AUTOSAR repo.")
    parser.add_argument("folder", help="target folder (created; must be empty)")
    parser.add_argument("--preset", choices=sorted(PRESETS), default="small")
    parser.add_argument("--seed", type=int, default=None)
    arguments = parser.parse_args()
    spec = PRESETS[arguments.preset]
    if arguments.seed is not None:
        spec = dataclasses.replace(spec, seed=arguments.seed)
    begin = time.perf_counter()
    truth = generate(arguments.folder, spec)
    elapsed = time.perf_counter() - begin
    print(f"generated in {elapsed:.2f} s: {truth.repo_root}")
    for key, value in truth.counts.items():
        print(f"  {key:18} {value}")


if __name__ == "__main__":
    main()
