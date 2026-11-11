"""Tests for the fake DaVinci command-line tools in tests/fakes."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil
import pytest

from porttool.core.interfaces import JobContext
from porttool.engine import run_sync
from tests.fakes import FAKE_SCRIPT, TOOLS, cmd_wrapper, fake_command, fake_davinci, fake_env

WAIT = 30.0


def run(tool: str, *args: str, **options: object) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [*fake_command(tool), *args],
        env=fake_env(**options),  # type: ignore[arg-type]
        capture_output=True,
        timeout=WAIT,
        check=False,
    )


def _kill(pid: int) -> None:
    try:
        psutil.Process(pid).kill()
    except psutil.NoSuchProcess:
        return


@pytest.mark.parametrize("tool", TOOLS)
def test_each_tool_runs_with_exit_code_zero_and_a_banner(tool: str) -> None:
    done = run(tool, "--anything", "at all")
    assert done.returncode == 0
    assert done.stdout == f"[fake] {tool} started with 2 argument(s)\n".encode()
    assert done.stderr == b""


def test_exit_code_and_lines_of_stdout_and_stderr() -> None:
    done = run("DVCfgCmd", exit_code=7, stdout=["first", "zweite Zeile ä", ""], stderr=["oops"])
    assert done.returncode == 7
    assert done.stdout.decode("utf-8") == "first\nzweite Zeile ä\n\n"
    assert done.stderr == b"oops\n"
    assert run("DVCfgCmd", stdout=[]).stdout == b""  # explicitly silent


def test_tool_from_the_script_name(tmp_path: Path) -> None:
    for name, expected in (("DVSwcGen.py", "DVSwcGen"), ("dvimex.py", "DVImEx")):
        copy = tmp_path / name
        shutil.copy(FAKE_SCRIPT, copy)
        done = subprocess.run(
            [sys.executable, str(copy), "x"], env=fake_env(), capture_output=True, timeout=WAIT
        )
        assert done.stdout == f"[fake] {expected} started with 1 argument(s)\n".encode()
    assert fake_davinci.detect_tool(["C:\\Vector\\DVCfgCmd.exe", "a"]) == ("DVCfgCmd", ["a"])
    assert fake_davinci.detect_tool(["x.py", "a", "--tool", "dvwspchecker", "b"]) == (
        "DVWspChecker",
        ["a", "b"],
    )


def test_unknown_tool_is_an_error() -> None:
    done = subprocess.run(
        [sys.executable, str(FAKE_SCRIPT), "--tool", "DVNothing"],
        env=fake_env(),
        capture_output=True,
        timeout=WAIT,
    )
    assert done.returncode == fake_davinci.EXIT_UNKNOWN_TOOL
    assert b"cannot tell which tool" in done.stderr
    with pytest.raises(ValueError, match="unknown fake tool"):
        fake_command("DVNothing")


def test_invocation_is_recorded(tmp_path: Path) -> None:
    record = tmp_path / "calls.jsonl"
    run("DVWspChecker", "--workspace", "C:\\a b\\ws.dcf", record_file=record)
    run("DVCfgCmd", record_file=record)
    calls = [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines()]
    assert [call["tool"] for call in calls] == ["DVWspChecker", "DVCfgCmd"]
    assert calls[0]["args"] == ["--workspace", "C:\\a b\\ws.dcf"]
    assert calls[1]["args"] == [] and calls[0]["pid"] != calls[1]["pid"]


def test_delay() -> None:
    begin = time.monotonic()
    done = run("DVCfgCmd", delay_s=0.4, stdout=["done"])
    assert time.monotonic() - begin >= 0.4
    assert done.returncode == 0 and done.stdout == b"done\n"


def test_slow_line_by_line_output() -> None:
    process = subprocess.Popen(
        fake_command("DVCfgCmd"),
        env=fake_env(stdout=["one", "two", "three"], line_delay_s=0.2),
        stdout=subprocess.PIPE,
    )
    assert process.stdout is not None
    begin = time.monotonic()
    assert process.stdout.readline() == b"one\n"  # arrives before the tool has finished
    first = time.monotonic() - begin
    assert process.stdout.read() == b"two\nthree\n"
    assert process.wait(WAIT) == 0
    assert time.monotonic() - begin >= first + 0.3


def test_hang_forever() -> None:
    process = subprocess.Popen(
        fake_command("DVCfgCmd"),
        env=fake_env(hang=True, stdout=["working"]),
        stdout=subprocess.PIPE,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline() == b"working\n"
        time.sleep(0.5)
        assert process.poll() is None  # still running, and it would be for ever
    finally:
        process.kill()
        process.wait(WAIT)
    assert process.returncode != 0


def test_crash() -> None:
    done = run("DVSwcGen", crash=True, stdout=["before the crash"])
    expected = 0xC0000005 if sys.platform == "win32" else 0xC0000005 & 0xFF
    assert done.returncode == expected
    assert done.stdout == b"before the crash\nDVSwcGen: writing resu"  # cut off mid-line
    assert b"Unhandled exception" in done.stderr
    assert run("DVSwcGen", crash=True, crash_code=9).returncode == 9


def test_license_error_text() -> None:
    done = run("DVCfgCmd", license_error=True)
    assert done.returncode == 1
    assert fake_davinci.DEFAULT_LICENSE_TEXT.encode() in done.stdout
    assert fake_davinci.DEFAULT_LICENSE_TEXT.encode() in done.stderr
    custom = run("DVCfgCmd", license_error="License server not reachable", exit_code=42)
    assert custom.returncode == 42
    assert custom.stdout == b"DVCfgCmd: License server not reachable\n"
    assert custom.stderr == b"License server not reachable\n"


def test_megabytes_of_output() -> None:
    done = run("DVCfgCmd", output_mb=3, stdout=[])
    assert done.returncode == 0
    assert len(done.stdout) == 3 * 1024 * 1024
    assert done.stdout.startswith(b"000000001 DVCfgCmd generating output ...")
    assert done.stdout.count(b"\n") > 25_000


def test_non_utf8_output() -> None:
    done = run("DVImEx", non_utf8=True, stdout=["plain"])
    assert done.returncode == 0
    assert done.stdout.startswith(b"plain\nGr\xf6\xdfe \xfcberschritten")
    with pytest.raises(UnicodeDecodeError):
        done.stdout.decode("utf-8")
    assert "Größe überschritten" in done.stdout.decode("cp1252")


def test_report_file(tmp_path: Path) -> None:
    report = tmp_path / "Bericht mit Leerzeichen ä.log"
    done = run("DVWspChecker", report_file=report, exit_code=2)
    assert done.returncode == 2
    assert report.read_text(encoding="utf-8") == "fake report written by DVWspChecker\n"
    run("DVWspChecker", report_file=report, report_text="<report errors='3'/>\n")
    assert report.read_text(encoding="utf-8") == "<report errors='3'/>\n"


def test_child_that_outlives_the_tool(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    done = run("DVCfgCmd", spawn_child_s=60, child_pid_file=pid_file, stdout=[])
    assert done.returncode == 0  # the tool itself is gone ...
    child_pid = int(pid_file.read_text())
    try:
        assert done.stdout == f"child pid: {child_pid}\n".encode()
        child = psutil.Process(child_pid)
        assert child.is_running() and child.status() != psutil.STATUS_ZOMBIE  # ... its child is not
    finally:
        _kill(child_pid)


def test_child_that_keeps_the_output_pipe_open(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    process = subprocess.Popen(
        fake_command("DVCfgCmd"),
        env=fake_env(
            spawn_child_s=60, child_pid_file=pid_file, child_inherits_pipes=True, stdout=[]
        ),
        stdout=subprocess.PIPE,
    )
    assert process.stdout is not None
    chunks: list[bytes] = []
    reader = threading.Thread(target=lambda: chunks.append(process.stdout.read()))  # type: ignore[union-attr]
    try:
        assert process.wait(WAIT) == 0
        reader.start()
        reader.join(0.5)
        # The tool has exited, but reading its output does not end: the child holds the pipe.
        assert reader.is_alive()
    finally:
        _kill(int(pid_file.read_text()))
        if reader.ident is not None:
            reader.join(WAIT)
        process.stdout.close()
    assert not reader.is_alive()
    assert chunks and chunks[0].startswith(b"child pid: ")


def test_fake_env_starts_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_DV_HANG", "1")
    monkeypatch.setenv("KEEP_ME", "yes")
    env = fake_env(exit_code=3)
    assert "FAKE_DV_HANG" not in env
    assert env["KEEP_ME"] == "yes" and env["FAKE_DV_EXIT"] == "3"
    assert env["FAKE_DV_PYTHON"] == sys.executable
    assert fake_env(base={"A": "b"}, hang=True) == {
        "A": "b",
        "FAKE_DV_PYTHON": sys.executable,
        "FAKE_DV_HANG": "1",
    }


def test_cmd_wrappers_exist_for_every_tool() -> None:
    for tool in TOOLS:
        text = cmd_wrapper(tool).read_bytes()
        assert f"--tool {tool} %*".encode() in text
        assert b"%~dp0fake_davinci.py" in text
        assert text.count(b"\r\n") == text.count(b"\n")  # CRLF line ends for cmd.exe


@pytest.mark.windows_only
@pytest.mark.skipif(sys.platform != "win32", reason="cmd.exe wrappers run on Windows only")
def test_cmd_wrapper_runs_the_fake_on_windows() -> None:
    done = subprocess.run(
        [str(cmd_wrapper("DVCfgCmd")), "a", "b c"],
        env=fake_env(exit_code=5),
        capture_output=True,
        timeout=WAIT,
    )
    assert done.returncode == 5
    assert done.stdout.strip() == b"[fake] DVCfgCmd started with 2 argument(s)"


def test_fake_tool_through_the_engine_with_live_output_and_cancel() -> None:
    """The way the davinci package will use it: asyncio subprocess inside an engine job."""
    lines: list[bytes] = []

    async def job(ctx: JobContext) -> int | None:
        process = await asyncio.create_subprocess_exec(
            *fake_command("DVCfgCmd"),
            env=fake_env(stdout=["line 1", "line 2"], hang=True),
            stdout=asyncio.subprocess.PIPE,
        )
        assert process.stdout is not None
        try:
            while len(lines) < 2:
                lines.append(await process.stdout.readline())
                ctx.progress(len(lines), 2, lines[-1].decode().strip())
            await asyncio.wait_for(process.wait(), timeout=0.3)  # it hangs: this times out
        except TimeoutError:
            process.kill()
            await process.wait()
        return process.returncode

    reports: list[str] = []
    code = run_sync(job, on_progress=lambda done, total, message: reports.append(message))
    assert lines == [b"line 1\n", b"line 2\n"]
    assert code is not None and code != 0
    assert reports[-1] == "line 2"
