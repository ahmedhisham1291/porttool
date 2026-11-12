"""The one function that starts a tool: output, log, timeout, kill, cancel, gate."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any

import psutil
import pytest

from porttool.core.config import ProjectConfig
from porttool.core.errors import JobCancelled, LicenseError, ToolRunError, ToolTimeoutError
from porttool.davinci import procrun
from porttool.davinci.procrun import (
    LineDecoder,
    ProcessRequest,
    ToolGate,
    is_alive,
    judge_line,
    orphans_of,
    run_process,
)
from porttool.davinci.settings import load_settings
from tests.fakes import fake_command, fake_env
from tests.unit.davinci.helpers import FakeCtx, run

RULES = load_settings(ProjectConfig(repo_root=Path(".")))[0].results


def _request(
    tmp_path: Path, *, tool: str = "DVCfgCmd", timeout: float = 20.0, **fake: Any
) -> ProcessRequest:
    options = {
        key: fake.pop(key)
        for key in ("encoding", "max_kept_lines", "leftover_children")
        if key in fake
    }
    return ProcessRequest(
        label=f"{tool} (test)",
        argv=(*fake_command(tool), "an argument"),
        cwd=tmp_path,
        env=fake_env(record_file=tmp_path / "record.jsonl", **fake),
        timeout_s=timeout,
        log_file=tmp_path / "logs" / "run.log",
        rules=RULES,
        **options,
    )


def _recorded_pids(tmp_path: Path) -> list[int]:
    lines = (tmp_path / "record.jsonl").read_text(encoding="utf-8").splitlines()
    return [int(json.loads(line)["pid"]) for line in lines]


def _pid_alive(pid: int) -> bool:
    try:
        return is_alive(psutil.Process(pid))
    except psutil.NoSuchProcess:
        return False


def _wait_dead(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while _pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    return not _pid_alive(pid)


@pytest.fixture(autouse=True)
def _short_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(procrun, "LEFTOVER_GRACE_S", 0.3)
    monkeypatch.setattr(procrun, "PIPE_DRAIN_S", 1.0)


# ------------------------------------------------------------------ plain runs


def test_output_of_both_streams_goes_to_log_tail_and_progress(tmp_path: Path) -> None:
    ctx = FakeCtx()
    request = _request(tmp_path, stdout=["one", "two"], stderr=["three"], exit_code=4)
    outcome = run(run_process(request, ctx))
    assert outcome.exit_code == 4
    assert outcome.lines_total == 3
    assert sorted(outcome.tail) == ["one", "three", "two"]
    assert outcome.spawn_error is None and not outcome.leftover and not outcome.survivors
    log = request.log_file.read_text(encoding="utf-8").splitlines()
    assert log[0].startswith("[porttool] command: ")
    assert "[out] one" in log and "[out] two" in log and "[err] three" in log
    assert log[-1].startswith("[porttool] exit code: 4; 3 line(s)")
    assert ctx.messages[0] == "Starting DVCfgCmd (test)"
    assert "ended with exit code 4" in ctx.messages[-1]
    assert not procrun.TOOL_GATE.busy


def test_matching_lines_carry_their_log_line_numbers(tmp_path: Path) -> None:
    request = _request(tmp_path, stdout=["fine", "ERROR: broken", "Warning: odd", "0 errors"])
    outcome = run(run_process(request, FakeCtx()))
    assert outcome.errors.total == 1 and outcome.warnings.total == 1
    log = request.log_file.read_text(encoding="utf-8").splitlines()
    error = outcome.errors.lines[0]
    assert log[error.line_no - 1] == "[out] ERROR: broken"
    assert log[outcome.warnings.lines[0].line_no - 1] == "[out] Warning: odd"


def test_only_the_first_twenty_matches_are_kept(tmp_path: Path) -> None:
    request = _request(tmp_path, stdout=[f"error {n}" for n in range(1, 31)])
    outcome = run(run_process(request, FakeCtx()))
    assert outcome.errors.total == 30
    assert [line.text for line in outcome.errors.lines] == [f"error {n}" for n in range(1, 21)]


def test_progress_is_throttled(tmp_path: Path) -> None:
    ctx = FakeCtx()
    outcome = run(run_process(_request(tmp_path, output_mb=0.5), ctx))
    assert outcome.lines_total > 5000
    assert len(ctx.messages) < 200  # far fewer than one per line


def test_program_that_does_not_exist_is_an_outcome_not_an_exception(tmp_path: Path) -> None:
    request = ProcessRequest(
        label="DVCfgCmd (test)",
        argv=(str(tmp_path / "no such tool.exe"),),
        cwd=tmp_path,
        env={},
        timeout_s=5,
        log_file=tmp_path / "run.log",
        rules=RULES,
    )
    outcome = run(run_process(request, FakeCtx()))
    assert outcome.exit_code is None
    assert outcome.spawn_error is not None and "FileNotFoundError" in outcome.spawn_error
    assert "could not be started" in request.log_file.read_text(encoding="utf-8")
    assert not procrun.TOOL_GATE.busy


def test_log_file_that_cannot_be_written_stops_before_the_tool_starts(tmp_path: Path) -> None:
    blocker = tmp_path / "logs"
    blocker.write_text("a file where the log folder should be", encoding="utf-8")
    with pytest.raises(ToolRunError, match="cannot be written"):
        run(run_process(_request(tmp_path), FakeCtx()))
    assert not (tmp_path / "record.jsonl").exists()
    assert not procrun.TOOL_GATE.busy


# ------------------------------------------------------------------ corner cases


def test_tool_prints_megabytes_memory_capped_log_complete(tmp_path: Path) -> None:
    request = _request(tmp_path, output_mb=6, max_kept_lines=100)
    outcome = run(run_process(request, FakeCtx()))
    assert outcome.exit_code == 0
    assert outcome.lines_total > 60_000
    assert len(outcome.tail) == 100  # memory holds the newest lines only
    assert request.log_file.stat().st_size > 6 * 1024 * 1024  # the log holds everything
    with open(request.log_file, encoding="utf-8") as stream:
        out_lines = sum(1 for line in stream if line.startswith("[out] "))
    assert out_lines == outcome.lines_total


def test_megabytes_without_a_line_end_do_not_grow_one_line(tmp_path: Path) -> None:
    decoder = LineDecoder()
    lines = decoder.feed(b"x" * (3 * procrun.MAX_LINE_BYTES))
    assert len(lines) == 1 and len(lines[0]) == 3 * procrun.MAX_LINE_BYTES
    assert decoder.flush() == []
    assert len(procrun.shorten(lines[0])) < 2100  # what is kept in memory is cut


def test_non_utf8_output_is_read_without_error(tmp_path: Path) -> None:
    request = _request(tmp_path, non_utf8=True, stdout=["Grüße in UTF-8"])
    outcome = run(run_process(request, FakeCtx()))
    assert outcome.exit_code == 0
    assert "Grüße in UTF-8" in outcome.tail
    odd = next(line for line in outcome.tail if "not UTF-8" in line)
    assert odd.startswith("Gr") and "berschritten" in odd
    assert "not UTF-8" in request.log_file.read_text(encoding="utf-8")


def test_non_utf8_output_with_a_named_encoding(tmp_path: Path) -> None:
    request = _request(tmp_path, non_utf8=True, encoding="cp1252")
    outcome = run(run_process(request, FakeCtx()))
    assert any(line.startswith("Größe überschritten") for line in outcome.tail)


def test_crash_exit_code_and_half_a_line(tmp_path: Path) -> None:
    request = _request(tmp_path, crash=True, crash_code=77)
    outcome = run(run_process(request, FakeCtx()))
    assert outcome.exit_code == 77
    assert "DVCfgCmd: writing resu" in outcome.tail  # the line that never ended
    assert outcome.errors.total == 0 or outcome.errors.lines  # stderr text is judged too


def test_tool_hangs_with_no_output_timeout_kills_it(tmp_path: Path) -> None:
    request = _request(tmp_path, hang=True, stdout=[], timeout=0.6)
    started = time.monotonic()
    with pytest.raises(ToolTimeoutError) as caught:
        run(run_process(request, FakeCtx()))
    assert time.monotonic() - started < 10
    assert "did not finish within 0.6 s" in str(caught.value)
    assert str(request.log_file) in str(caught.value)
    (pid,) = _recorded_pids(tmp_path)
    assert not _pid_alive(pid)
    log = request.log_file.read_text(encoding="utf-8")
    assert "stopped after" in log and "timeout after 0.6 s" in log and "killed: " in log
    assert not procrun.TOOL_GATE.busy


def test_timeout_kills_the_child_of_the_tool_too(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    request = _request(
        tmp_path, hang=True, spawn_child_s=60, child_pid_file=pid_file, timeout=1.0
    )
    with pytest.raises(ToolTimeoutError) as caught:
        run(run_process(request, FakeCtx()))
    child = int(pid_file.read_text(encoding="utf-8"))
    (parent,) = _recorded_pids(tmp_path)
    assert _wait_dead(child) and not _pid_alive(parent)
    assert "2 process(es) killed" in str(caught.value)


def test_child_that_holds_the_pipes_does_not_block_the_timeout(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    request = _request(
        tmp_path,
        hang=True,
        spawn_child_s=60,
        child_pid_file=pid_file,
        child_inherits_pipes=True,
        timeout=1.0,
    )
    with pytest.raises(ToolTimeoutError):
        run(run_process(request, FakeCtx()))
    assert _wait_dead(int(pid_file.read_text(encoding="utf-8")))


def test_cancel_during_a_run_kills_the_tree(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    ctx = FakeCtx()
    request = _request(tmp_path, hang=True, spawn_child_s=60, child_pid_file=pid_file)
    timer = threading.Timer(0.8, ctx.cancel.set)
    timer.start()
    try:
        with pytest.raises(JobCancelled):
            run(run_process(request, ctx))
    finally:
        timer.cancel()
    (parent,) = _recorded_pids(tmp_path)
    assert not _pid_alive(parent)
    assert _wait_dead(int(pid_file.read_text(encoding="utf-8")))
    assert "cancelled" in request.log_file.read_text(encoding="utf-8")
    assert not procrun.TOOL_GATE.busy


def test_asyncio_cancellation_kills_the_tree(tmp_path: Path) -> None:
    request = _request(tmp_path, hang=True)

    async def scenario() -> None:
        task = asyncio.ensure_future(run_process(request, FakeCtx()))
        for _ in range(400):  # until the fake tool has started
            if (tmp_path / "record.jsonl").exists():
                break
            await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(scenario())
    (pid,) = _recorded_pids(tmp_path)
    assert not _pid_alive(pid)
    assert not procrun.TOOL_GATE.busy


def test_cancelled_before_the_start_runs_nothing(tmp_path: Path) -> None:
    ctx = FakeCtx()
    ctx.cancel.set()
    with pytest.raises(JobCancelled):
        run(run_process(_request(tmp_path), ctx))
    assert not (tmp_path / "record.jsonl").exists()


def test_child_that_outlives_the_parent_is_stopped(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    request = _request(tmp_path, spawn_child_s=60, child_pid_file=pid_file, delay_s=0.6)
    outcome = run(run_process(request, FakeCtx()))
    child = int(pid_file.read_text(encoding="utf-8"))
    assert outcome.exit_code == 0
    assert [info.pid for info in outcome.leftover] == [child]
    assert outcome.leftover_killed and not outcome.survivors
    assert _wait_dead(child)
    assert "still running after the tool ended, were stopped" in request.log_file.read_text(
        encoding="utf-8"
    )


def test_child_that_outlives_the_parent_and_holds_the_pipes(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    request = _request(
        tmp_path,
        spawn_child_s=60,
        child_pid_file=pid_file,
        child_inherits_pipes=True,
        delay_s=0.6,
    )
    started = time.monotonic()
    outcome = run(run_process(request, FakeCtx()))
    assert time.monotonic() - started < 15
    assert outcome.exit_code == 0 and outcome.leftover_killed
    assert not outcome.pipes_left_open  # the kill closed them
    assert _wait_dead(int(pid_file.read_text(encoding="utf-8")))


def test_child_that_outlives_the_parent_can_be_left_running(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    request = _request(
        tmp_path,
        spawn_child_s=60,
        child_pid_file=pid_file,
        child_inherits_pipes=True,
        delay_s=0.6,
        leftover_children="report",
    )
    outcome = run(run_process(request, FakeCtx()))
    child = int(pid_file.read_text(encoding="utf-8"))
    try:
        assert [info.pid for info in outcome.leftover] == [child]
        assert not outcome.leftover_killed
        assert outcome.pipes_left_open  # reading was given up, nothing hangs
        assert _pid_alive(child)
    finally:
        psutil.Process(child).kill()


def test_two_runs_requested_at_once_are_serialised(tmp_path: Path) -> None:
    events: list[tuple[str, str]] = []

    class Ctx(FakeCtx):
        def __init__(self, name: str) -> None:
            super().__init__()
            self.name = name

        def progress(self, done: int, total: int, message: str = "") -> None:
            if message.startswith("Starting"):
                events.append((self.name, "start"))
            if "ended with exit code" in message:
                events.append((self.name, "end"))

    async def both() -> None:
        first = _request(tmp_path / "a", delay_s=0.5)
        second = _request(tmp_path / "b", delay_s=0.5)
        await asyncio.gather(run_process(first, Ctx("a")), run_process(second, Ctx("b")))

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    started = time.monotonic()
    run(both())
    assert time.monotonic() - started >= 1.0  # one after the other, not side by side
    assert events == [("a", "start"), ("a", "end"), ("b", "start"), ("b", "end")]


def test_two_runs_from_two_event_loops_are_serialised_too(tmp_path: Path) -> None:
    spans: list[tuple[float, float]] = []

    def worker(name: str) -> None:
        folder = tmp_path / name
        folder.mkdir()
        before = time.monotonic()
        outcome = run(run_process(_request(folder, delay_s=0.4), FakeCtx()))
        end = time.monotonic()
        spans.append((end - outcome.elapsed_s, end))
        assert before <= end

    threads = [threading.Thread(target=worker, args=(name,)) for name in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert len(spans) == 2
    (first_start, first_end), (second_start, _) = sorted(spans)
    assert first_start < first_end <= second_start + 0.05


def test_waiting_for_the_gate_can_be_cancelled() -> None:
    async def scenario() -> None:
        gate = ToolGate()
        holder, waiter = FakeCtx(), FakeCtx()
        await gate.acquire(holder)
        task = asyncio.ensure_future(gate.acquire(waiter))
        await asyncio.sleep(0.1)
        assert not task.done()
        waiter.cancel.set()
        with pytest.raises(JobCancelled):
            await task
        gate.release()
        await gate.acquire(FakeCtx())  # the cancelled waiter left no ticket behind
        assert gate.busy

    run(scenario())


# ------------------------------------------------------------------ license


@pytest.mark.parametrize(
    "text",
    [
        "License not found for product DaVinci",
        "The license has expired on 2026-01-01",
        "Could not connect to the license server (server unreachable)",
    ],
    ids=["missing", "expired", "server-unreachable"],
)
def test_license_text_raises_license_error(tmp_path: Path, text: str) -> None:
    request = _request(tmp_path, license_error=text)
    with pytest.raises(LicenseError) as caught:
        run(run_process(request, FakeCtx()))
    assert text in str(caught.value)
    assert str(request.log_file) in str(caught.value)
    assert caught.value.code == "PT-E620"
    assert not procrun.TOOL_GATE.busy


def test_default_fake_license_text_is_recognised(tmp_path: Path) -> None:
    with pytest.raises(LicenseError):
        run(run_process(_request(tmp_path, license_error=True), FakeCtx()))


def test_license_line_then_hang_is_a_license_error_not_a_timeout(tmp_path: Path) -> None:
    request = _request(tmp_path, stdout=["Waiting: license not found"], hang=True, timeout=0.6)
    with pytest.raises(LicenseError, match="did not finish within 0.6 s"):
        run(run_process(request, FakeCtx()))
    (pid,) = _recorded_pids(tmp_path)
    assert not _pid_alive(pid)


# ------------------------------------------------------------------ small parts


def test_line_decoder_splits_lines_across_chunks() -> None:
    decoder = LineDecoder()
    assert decoder.feed(b"\xef\xbb\xbffirst\r\nsec") == ["first"]
    more = decoder.feed(b"ond\n\nprogress 1\rprogress 2\n")
    assert more == ["second", "", "progress 1", "progress 2"]
    assert decoder.feed(b"tail") == []
    assert decoder.flush() == ["tail"]
    assert decoder.flush() == []


def test_line_decoder_with_a_named_encoding_handles_utf16() -> None:
    decoder = LineDecoder("utf-16-le")
    data = "Zeile ä\r\nzwei\nrest".encode("utf-16-le")
    lines = decoder.feed(data[:5]) + decoder.feed(data[5:])
    assert lines == ["Zeile ä", "zwei"]
    assert decoder.flush() == ["rest"]


def test_judge_line_license_first_and_zero_counts_ignored() -> None:
    assert judge_line(RULES, "ERROR: license expired") == "license"
    assert judge_line(RULES, "Error: boom") == "error"
    assert judge_line(RULES, "warning C4100") == "warning"
    assert judge_line(RULES, "Finished: 0 errors, 0 warnings") is None
    assert judge_line(RULES, "Finished: 0 errors, 2 warnings") == "warning"
    assert judge_line(RULES, "Finished: 3 errors, 0 warnings") == "error"
    assert judge_line(RULES, "Errors: 0  Warnings: 10") == "warning"
    assert judge_line(RULES, "10 errors") == "error"
    assert judge_line(RULES, "all good") is None


class _FakeProcess:
    def __init__(self, pid: int, ppid: int, created: float) -> None:
        self.pid = pid
        self._ppid = ppid
        self._created = created

    def ppid(self) -> int:
        return self._ppid

    def create_time(self) -> float:
        if self._created < 0:
            raise psutil.AccessDenied()
        return self._created


def test_orphans_are_found_by_parent_id_and_creation_time() -> None:
    listing = [
        _FakeProcess(10, 5, 105.0),  # child, created while the tool lived
        _FakeProcess(11, 5, 300.0),  # child of a later process with the same id
        _FakeProcess(12, 5, 50.0),  # child of an earlier process with the same id
        _FakeProcess(13, 6, 105.0),  # someone else's child
        _FakeProcess(14, 5, -1.0),  # cannot be inspected
    ]
    found = orphans_of(5, created_from=100.0, created_until=200.0, processes=listing)
    assert [process.pid for process in found] == [10]


def test_orphans_of_scans_the_real_process_list() -> None:
    assert orphans_of(2**22 + 12345, 0.0, time.time()) == []
