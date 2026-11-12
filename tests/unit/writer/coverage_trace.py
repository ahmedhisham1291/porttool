"""Statement coverage of porttool/writer without pytest-cov (not installed in the sandbox).

    python3 -m tests.unit.writer.coverage_trace [--missing]

Runs the writer tests under ``sys.settrace`` / ``threading.settrace`` (text work runs in
worker threads) and prints, per module, the share of statements that were executed.
Throw-away tool, not a test. Same method as tests/unit/reader/coverage_trace.py.
"""

from __future__ import annotations

import ast
import glob
import os
import sys
import threading
from types import FrameType
from typing import Any

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
PREFIX = os.path.join(ROOT, "porttool", "writer") + os.sep

_hits: dict[str, set[int]] = {}


def _line_tracer(frame: FrameType, event: str, arg: Any) -> Any:
    if event == "line":
        _hits[frame.f_code.co_filename].add(frame.f_lineno)
    return _line_tracer


def _call_tracer(frame: FrameType, event: str, arg: Any) -> Any:
    name = frame.f_code.co_filename
    if not name.startswith(PREFIX):
        return None
    _hits.setdefault(name, set()).add(frame.f_lineno)
    return _line_tracer


def _statements(path: str) -> set[int]:
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.stmt):
            continue
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            continue  # a docstring
        lines.add(node.lineno)
    return lines


def main() -> int:
    os.chdir(ROOT)
    threading.settrace(_call_tracer)
    sys.settrace(_call_tracer)
    import pytest

    exit_code = int(
        pytest.main(["tests/unit/writer", "-q", "-p", "no:cacheprovider", "-m", "not slow"])
    )
    sys.settrace(None)
    threading.settrace(None)
    total = executed = 0
    for path in sorted(glob.glob(PREFIX + "*.py")):
        statements = _statements(path)
        hit = _hits.get(path, set()) & statements
        total += len(statements)
        executed += len(hit)
        share = 100.0 * len(hit) / max(1, len(statements))
        print(f"{os.path.basename(path):16} {len(statements):5} statements {share:6.1f}%")
        if "--missing" in sys.argv:
            print("   missing:", sorted(statements - hit))
    print(f"TOTAL {total} statements, {100.0 * executed / max(1, total):.1f}% executed")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
