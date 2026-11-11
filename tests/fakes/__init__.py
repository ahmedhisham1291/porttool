"""Fake DaVinci command-line tools for tests. See ``fake_davinci.py`` for what they can do.

::

    from tests.fakes import fake_command, fake_env

    process = await asyncio.create_subprocess_exec(
        *fake_command("DVCfgCmd"), "any", "arguments", env=fake_env(exit_code=3, stdout=["a", "b"])
    )
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

TOOLS = ("DVWspChecker", "DVCfgCmd", "DVSwcGen", "DVImEx")
FAKES_DIR = Path(__file__).resolve().parent
FAKE_SCRIPT = FAKES_DIR / "fake_davinci.py"


def _check(tool: str) -> None:
    if tool not in TOOLS:
        raise ValueError(f"unknown fake tool {tool!r}; choose one of {', '.join(TOOLS)}")


def fake_command(tool: str) -> list[str]:
    """The command list that starts the fake ``tool`` with the running Python.

    Append the tool's own arguments. Works on every platform.
    """
    _check(tool)
    return [sys.executable, str(FAKE_SCRIPT), "--tool", tool]


def cmd_wrapper(tool: str) -> Path:
    """Path of the Windows ``.cmd`` wrapper of ``tool`` (starts cmd.exe, then Python).

    Windows only. The wrapper runs the Python named by FAKE_DV_PYTHON (``fake_env`` sets
    it to the running interpreter), so the fake has a real process tree: cmd.exe -> python.
    """
    _check(tool)
    return FAKES_DIR / f"{tool}.cmd"


def fake_env(
    *,
    exit_code: int | None = None,
    stdout: Sequence[str] | None = None,
    stderr: Sequence[str] | None = None,
    line_delay_s: float | None = None,
    delay_s: float | None = None,
    hang: bool = False,
    crash: bool = False,
    crash_code: int | None = None,
    license_error: bool | str = False,
    output_mb: float | None = None,
    non_utf8: bool = False,
    spawn_child_s: float | None = None,
    child_pid_file: str | os.PathLike[str] | None = None,
    child_inherits_pipes: bool = False,
    report_file: str | os.PathLike[str] | None = None,
    report_text: str | None = None,
    record_file: str | os.PathLike[str] | None = None,
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """A process environment that makes the fake behave as asked.

    Starts from ``base`` (default: this process's environment) with every FAKE_DV_*
    variable removed, then sets the ones that follow from the arguments.
    """
    env = {key: value for key, value in (os.environ if base is None else base).items()}
    for key in [key for key in env if key.startswith("FAKE_DV_")]:
        del env[key]
    env["FAKE_DV_PYTHON"] = sys.executable

    def put(name: str, value: object | None) -> None:
        if value is not None:
            env[f"FAKE_DV_{name}"] = str(value)

    put("EXIT", exit_code)
    put("STDOUT", None if stdout is None else "\n".join(stdout))
    put("STDERR", None if stderr is None else "\n".join(stderr))
    put("LINE_DELAY", line_delay_s)
    put("DELAY", delay_s)
    put("HANG", "1" if hang else None)
    put("CRASH", "1" if crash else None)
    put("CRASH_CODE", crash_code)
    put("LICENSE_ERROR", "1" if license_error is True else (license_error or None))
    put("OUTPUT_MB", output_mb)
    put("NON_UTF8", "1" if non_utf8 else None)
    put("SPAWN_CHILD", spawn_child_s)
    put("CHILD_PID_FILE", None if child_pid_file is None else os.fspath(child_pid_file))
    put("CHILD_INHERITS_PIPES", "1" if child_inherits_pipes else None)
    put("REPORT_FILE", None if report_file is None else os.fspath(report_file))
    put("REPORT_TEXT", report_text)
    put("RECORD_FILE", None if record_file is None else os.fspath(record_file))
    return env
