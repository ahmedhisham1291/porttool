#!/usr/bin/env python3
"""Fake DaVinci command-line tools for tests.

One script impersonates DVWspChecker, DVCfgCmd, DVSwcGen and DVImEx. Which one it is
comes from ``--tool NAME`` or, without that flag, from the name it was started under
(``DVCfgCmd.py``, ``DVCfgCmd.cmd`` ...). Every other argument is accepted and ignored.

PENDING-DISCOVERY: the real tools' flags, output texts, exit codes and license messages
are not known yet. This fake has no opinion about them: it does exactly what these
environment variables say, in this order.

FAKE_DV_RECORD_FILE     append one JSON line (tool, args, cwd, pid) to this file
FAKE_DV_LICENSE_ERROR   print a license error on stdout and stderr and exit with
                        FAKE_DV_EXIT (default 1). "1" prints a default text, any other
                        value is printed as the text
FAKE_DV_STDOUT          text for stdout (lines separated by newlines). Unset: one banner line
FAKE_DV_STDERR          text for stderr
FAKE_DV_LINE_DELAY      seconds to wait after each stdout line (a slow, live log)
FAKE_DV_OUTPUT_MB       write this many megabytes of numbered lines to stdout
FAKE_DV_NON_UTF8        "1": write a line that is not valid UTF-8 (cp1252 umlauts, 0xFF)
FAKE_DV_REPORT_FILE     write a report/log file to this path ...
FAKE_DV_REPORT_TEXT     ... with this content (default: a one-line fake report)
FAKE_DV_SPAWN_CHILD     start a child process that lives this many seconds and outlives
                        this process; its pid is printed as "child pid: N"
FAKE_DV_CHILD_PID_FILE  also write the child's pid to this file
FAKE_DV_CHILD_INHERITS_PIPES  "1": the child keeps this process's stdout/stderr open
FAKE_DV_DELAY           seconds to wait before finishing
FAKE_DV_CRASH           "1": die abruptly in the middle of a line, without cleanup, with
                        FAKE_DV_CRASH_CODE (default 3221225477 = 0xC0000005)
FAKE_DV_HANG            "1": never finish
FAKE_DV_EXIT            exit code (default 0)

Standard library only; nothing is imported from porttool.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

TOOLS = ("DVWspChecker", "DVCfgCmd", "DVSwcGen", "DVImEx")
DEFAULT_LICENSE_TEXT = (
    "FAKE license error: no valid license found (PENDING-DISCOVERY: not the real message)"
)
EXIT_UNKNOWN_TOOL = 64
_ACCESS_VIOLATION = 0xC0000005


def detect_tool(argv: list[str]) -> tuple[str | None, list[str]]:
    """Return the impersonated tool and the remaining arguments."""
    args = list(argv[1:])
    # Split on both separators so a Windows-style path gives the same answer everywhere.
    name = argv[0].replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0] if argv else ""
    if "--tool" in args:
        index = args.index("--tool")
        name = args[index + 1] if index + 1 < len(args) else ""
        del args[index : index + 2]
    for tool in TOOLS:
        if tool.lower() == name.lower():
            return tool, args
    return None, args


def _env(name: str) -> str | None:
    value = os.environ.get(f"FAKE_DV_{name}")
    return value if value else None


def _flag(name: str) -> bool:
    return os.environ.get(f"FAKE_DV_{name}", "") not in ("", "0")


def _out(data: bytes) -> None:
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def _err(data: bytes) -> None:
    sys.stderr.buffer.write(data)
    sys.stderr.buffer.flush()


def _lines(text: str) -> list[bytes]:
    return [line.encode("utf-8") + b"\n" for line in text.split("\n")] if text else []


def _spawn_child(seconds: str) -> int:
    inherit = _flag("CHILD_INHERITS_PIPES")
    child = subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "--child", seconds],
        stdin=subprocess.DEVNULL,
        stdout=None if inherit else subprocess.DEVNULL,
        stderr=None if inherit else subprocess.DEVNULL,
    )
    pid_file = _env("CHILD_PID_FILE")
    if pid_file:
        Path(pid_file).write_text(str(child.pid), encoding="utf-8")
    return child.pid


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[1] == "--child":
        time.sleep(float(argv[2]))
        return 0

    tool, args = detect_tool(argv)
    if tool is None:
        _err(b"fake_davinci: cannot tell which tool to impersonate; pass --tool NAME\n")
        return EXIT_UNKNOWN_TOOL

    record_file = _env("RECORD_FILE")
    if record_file:
        entry = {"tool": tool, "args": args, "cwd": os.getcwd(), "pid": os.getpid()}
        with open(record_file, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry) + "\n")

    exit_code = int(_env("EXIT") or "0")

    license_error = _env("LICENSE_ERROR")
    if license_error:
        text = DEFAULT_LICENSE_TEXT if license_error == "1" else license_error
        _out(f"{tool}: {text}\n".encode())
        _err(f"{text}\n".encode())
        return exit_code or 1

    stdout_text = os.environ.get("FAKE_DV_STDOUT")
    if stdout_text is None:
        stdout_text = f"[fake] {tool} started with {len(args)} argument(s)"
    line_delay = float(_env("LINE_DELAY") or "0")
    for line in _lines(stdout_text):
        _out(line)
        if line_delay:
            time.sleep(line_delay)
    for line in _lines(os.environ.get("FAKE_DV_STDERR", "")):
        _err(line)

    megabytes = float(_env("OUTPUT_MB") or "0")
    if megabytes:
        target = int(megabytes * 1024 * 1024)
        written = 0
        number = 0
        while written < target:
            number += 1
            line = f"{number:09d} {tool} generating output ".encode() + b"." * 60 + b"\n"
            line = line[: target - written]
            sys.stdout.buffer.write(line)
            written += len(line)
        sys.stdout.buffer.flush()

    if _flag("NON_UTF8"):
        _out(b"Gr\xf6\xdfe \xfcberschritten: \xff\xfe not UTF-8\n")

    report_file = _env("REPORT_FILE")
    if report_file:
        text = os.environ.get("FAKE_DV_REPORT_TEXT", f"fake report written by {tool}\n")
        Path(report_file).write_text(text, encoding="utf-8")

    child_seconds = _env("SPAWN_CHILD")
    if child_seconds:
        _out(f"child pid: {_spawn_child(child_seconds)}\n".encode())

    delay = float(_env("DELAY") or "0")
    if delay:
        time.sleep(delay)

    if _flag("CRASH"):
        _out(f"{tool}: writing resu".encode())  # dies in the middle of a line
        _err(b"Unhandled exception at 0x00007FF6 (fake crash)\n")
        code = int(_env("CRASH_CODE") or str(_ACCESS_VIOLATION))
        # os._exit takes a signed 32-bit value: 0xC0000005 must be passed as -1073741819.
        os._exit(code - 2**32 if code >= 2**31 else code)

    if _flag("HANG"):
        while True:
            time.sleep(3600)

    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
