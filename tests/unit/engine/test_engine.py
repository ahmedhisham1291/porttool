"""Tests for porttool.engine: jobs, progress, cancellation, shutdown."""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from porttool import engine as engine_package
from porttool.core.errors import JobCancelled, PortToolError
from porttool.core.interfaces import JobContext
from porttool.engine import Engine, JobHandle, JobState, run_sync

WAIT = 20.0  # generous: the sandbox has two shared cores
REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def engine() -> Iterator[Engine]:
    instance = Engine()
    instance.start()
    yield instance
    instance.shutdown()


async def _answer(ctx: JobContext) -> str:
    return "ok"


def _state(handle: JobHandle[object] | JobHandle[None] | JobHandle[str]) -> JobState:
    """Read the state through a call: mypy must not assume it never changes."""
    return handle.state


def _engine_threads() -> set[threading.Thread]:
    return {t for t in threading.enumerate() if t.name.startswith("porttool-")}


# --------------------------------------------------------------------------- running jobs


def test_sample_job_runs_with_progress_and_result(engine: Engine) -> None:
    reports: list[tuple[int, int, str]] = []
    first_delivered = threading.Event()

    def on_progress(done: int, total: int, message: str) -> None:
        reports.append((done, total, message))
        first_delivered.set()

    async def job(ctx: JobContext) -> int:
        def work() -> int:
            total = 0
            for step in range(1, 6):
                ctx.check_cancelled()
                total += step
                ctx.progress(step, 5, f"step {step}")
                first_delivered.wait(WAIT)
            return total

        return await ctx.run_blocking(work)

    handle = engine.submit(job, on_progress=on_progress, name="sum")
    assert handle.result(WAIT) == 15
    assert handle.name == "sum"
    assert handle.done() and not handle.cancelled()
    assert handle.state is JobState.DONE
    assert handle.exception() is None
    assert reports[0] == (1, 5, "step 1")  # the first report is delivered at once
    assert reports[-1] == (5, 5, "step 5")  # and the last one always
    assert 2 <= len(reports) <= 5
    assert handle.progress == (5, 5, "step 5")
    assert handle.future.result() == 15


def test_job_runs_on_the_engine_thread_and_blocking_work_in_the_pool(engine: Engine) -> None:
    async def job(ctx: JobContext) -> tuple[str, str]:
        return threading.current_thread().name, await ctx.run_blocking(
            lambda: threading.current_thread().name
        )

    loop_thread, worker_thread = engine.submit(job).result(WAIT)
    assert loop_thread == "porttool-engine"
    assert worker_thread.startswith("porttool-worker")


def test_job_name_defaults_to_the_function_name(engine: Engine) -> None:
    handle = engine.submit(_answer)
    assert handle.name == "_answer"
    assert handle.result(WAIT) == "ok"
    assert "_answer" in repr(handle) and "done" in repr(handle)


def test_a_job_that_raises_surfaces_through_the_handle_and_the_loop_survives(
    engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    async def bad(ctx: JobContext) -> None:
        raise ValueError("boom in job")

    with caplog.at_level("ERROR", logger="porttool"):
        handle = engine.submit(bad, name="bad job")
        with pytest.raises(ValueError, match="boom in job"):
            handle.result(WAIT)
    assert handle.done() and handle.state is JobState.FAILED and not handle.cancelled()
    assert isinstance(handle.exception(), ValueError)
    assert handle.status().error == "ValueError: boom in job"
    assert "job 'bad job'" in caplog.text and "failed" in caplog.text
    assert "Traceback" in caplog.text and "ValueError: boom in job" in caplog.text
    # The engine is still alive.
    assert engine.submit(_answer).result(WAIT) == "ok"


def test_an_exception_in_a_worker_function_surfaces_through_the_handle(engine: Engine) -> None:
    async def job(ctx: JobContext) -> float:
        return await ctx.run_blocking(lambda: 1 / 0)

    handle = engine.submit(job)
    with pytest.raises(ZeroDivisionError):
        handle.result(WAIT)
    assert engine.submit(_answer).result(WAIT) == "ok"


def test_a_porttool_error_keeps_its_code_in_the_status(engine: Engine) -> None:
    async def job(ctx: JobContext) -> None:
        raise PortToolError("the repo is gone")

    handle = engine.submit(job)
    assert isinstance(handle.exception(WAIT), PortToolError)
    assert handle.status().error == "[PT-E000] the repo is gone"


def test_a_job_that_calls_sys_exit_does_not_stop_the_engine(engine: Engine) -> None:
    async def exits_on_loop(ctx: JobContext) -> None:
        sys.exit(3)

    async def exits_in_worker(ctx: JobContext) -> None:
        await ctx.run_blocking(lambda: sys.exit(4))

    async def interrupts(ctx: JobContext) -> None:
        raise KeyboardInterrupt

    for job in (exits_on_loop, exits_in_worker, interrupts):
        handle = engine.submit(job)
        with pytest.raises(PortToolError, match="tried to stop the program"):
            handle.result(WAIT)
        assert handle.state is JobState.FAILED
    assert engine.running
    assert engine.submit(_answer).result(WAIT) == "ok"


def test_a_job_function_that_is_not_async_fails_cleanly(engine: Engine) -> None:
    def not_async(ctx: JobContext) -> int:
        return 5

    handle: JobHandle[int] = engine.submit(not_async)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        handle.result(WAIT)
    assert engine.submit(_answer).result(WAIT) == "ok"


def test_two_hundred_jobs_submitted_at_once(engine: Engine) -> None:
    def make(number: int) -> engine_package.JobFn[int]:
        async def job(ctx: JobContext) -> int:
            await asyncio.sleep(0)
            ctx.progress(1, 2)
            squared = await ctx.run_blocking(lambda: number * number)
            ctx.progress(2, 2)
            return squared

        return job

    handles = [engine.submit(make(number), name=f"job {number}") for number in range(200)]
    assert [handle.result(WAIT) for handle in handles] == [n * n for n in range(200)]
    assert len({handle.id for handle in handles}) == 200
    assert all(handle.state is JobState.DONE for handle in handles)
    assert all(handle.progress == (2, 2, "") for handle in handles)
    workers = [t for t in threading.enumerate() if t.name.startswith("porttool-worker")]
    assert 1 <= len(workers) <= engine.max_workers


def test_pool_size_follows_the_cpu_count(monkeypatch: pytest.MonkeyPatch) -> None:
    for cpus, expected in ((2, 6), (8, 12), (64, 32), (None, 5)):
        monkeypatch.setattr(os, "cpu_count", lambda cpus=cpus: cpus)
        assert Engine().max_workers == expected
    assert Engine(max_workers=3).max_workers == 3


def test_the_engine_thread_can_run_subprocesses(engine: Engine) -> None:
    async def job(ctx: JobContext) -> tuple[int | None, bytes]:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "print('hello from the child')",
            stdout=asyncio.subprocess.PIPE,
        )
        out, _ = await process.communicate()
        return process.returncode, out.strip()

    assert engine.submit(job).result(WAIT) == (0, b"hello from the child")


# --------------------------------------------------------------------------- handle accessors


def test_handle_state_goes_from_queued_to_running_to_done() -> None:
    gate = threading.Event()
    started = threading.Event()

    async def blocks_the_loop(ctx: JobContext) -> None:
        started.set()
        time.sleep(0.3)  # noqa: ASYNC251 - deliberately blocks the loop to hold the next job

    async def job(ctx: JobContext) -> str:
        ctx.progress(1, 3, "waiting")
        await ctx.run_blocking(lambda: gate.wait(WAIT))
        return "finished"

    with Engine() as engine:
        engine.submit(blocks_the_loop)
        assert started.wait(WAIT)
        handle = engine.submit(job, name="stateful")
        assert handle.state is JobState.QUEUED  # the loop is busy with the first job
        assert handle.progress is None and not handle.done()
        with pytest.raises(TimeoutError):
            handle.result(timeout=0)
        with pytest.raises(TimeoutError):
            handle.exception(timeout=0)
        deadline = time.monotonic() + WAIT
        while _state(handle) is JobState.QUEUED and time.monotonic() < deadline:
            time.sleep(0.01)
        assert _state(handle) is JobState.RUNNING
        while handle.progress is None and time.monotonic() < deadline:
            time.sleep(0.01)
        status = handle.status()
        assert status.id == handle.id and status.name == "stateful"
        assert status.state is JobState.RUNNING and status.state.value == "running"
        assert status.progress == (1, 3, "waiting")
        assert status.cancel_requested is False and status.error is None
        gate.set()
        assert handle.result(WAIT) == "finished"
        assert handle.status().state is JobState.DONE


def test_progress_is_recorded_without_a_callback(engine: Engine) -> None:
    async def job(ctx: JobContext) -> None:
        for step in range(50):
            ctx.progress(step + 1, 50, "polling client")

    handle = engine.submit(job)
    handle.result(WAIT)
    assert handle.progress == (50, 50, "polling client")


def test_jobs_can_be_looked_up_by_id() -> None:
    with Engine(keep_finished=3) as engine:
        handles = [engine.submit(_answer, name=f"job {n}") for n in range(5)]
        for handle in handles:
            handle.result(WAIT)
        assert engine.get("no such id") is None
        # Only the newest three finished jobs are kept.
        assert [h.name for h in engine.jobs()] == ["job 2", "job 3", "job 4"]
        assert engine.get(handles[4].id) is handles[4]
        assert engine.get(handles[0].id) is None
        assert handles[0].result() == "ok"  # a handle the caller kept still works

        gate = threading.Event()

        async def waits(ctx: JobContext) -> None:
            await ctx.run_blocking(lambda: gate.wait(WAIT))

        running = engine.submit(waits, name="running")
        assert engine.get(running.id) is running
        assert engine.jobs()[-1] is running
        gate.set()
        running.result(WAIT)


def test_done_callback_runs_on_finish_and_at_once_when_already_finished(
    engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    gate = threading.Event()
    seen: list[tuple[str, JobState]] = []
    called = threading.Event()

    async def job(ctx: JobContext) -> None:
        await ctx.run_blocking(lambda: gate.wait(WAIT))

    def on_done(handle: JobHandle[None]) -> None:
        seen.append((threading.current_thread().name, handle.state))
        called.set()

    handle = engine.submit(job)
    handle.add_done_callback(on_done)
    assert not called.is_set()
    gate.set()
    assert called.wait(WAIT)
    assert seen == [("porttool-engine", JobState.DONE)]
    handle.add_done_callback(on_done)  # already finished: called at once, on this thread
    assert seen[1] == (threading.current_thread().name, JobState.DONE)

    def broken(handle: JobHandle[None]) -> None:
        raise RuntimeError("boom in done callback")

    with caplog.at_level("ERROR", logger="porttool"):
        handle.add_done_callback(broken)
    assert "done callback" in caplog.text and "boom in done callback" in caplog.text
    assert engine.submit(_answer).result(WAIT) == "ok"


def test_waiting_for_a_job_on_the_engine_thread_is_refused(engine: Engine) -> None:
    async def outer(ctx: JobContext) -> str:
        inner = engine.submit(_answer)
        return inner.result()  # would deadlock: the engine thread waits for itself

    with pytest.raises(PortToolError, match="would block the engine"):
        engine.submit(outer).result(WAIT)

    async def outer_sync(ctx: JobContext) -> str:
        return engine.run_sync(_answer)

    with pytest.raises(PortToolError, match="must not be called from inside a job"):
        engine.submit(outer_sync).result(WAIT)

    async def stops(ctx: JobContext) -> None:
        engine.shutdown()

    with pytest.raises(PortToolError, match="must not be called from inside a job"):
        engine.submit(stops).result(WAIT)
    assert engine.submit(_answer).result(WAIT) == "ok"


# --------------------------------------------------------------------------- progress


def test_progress_throttling_at_most_ten_per_second_and_last_one_delivered(
    engine: Engine,
) -> None:
    stamps: list[float] = []
    reports: list[tuple[int, int, str]] = []

    def on_progress(done: int, total: int, message: str) -> None:
        stamps.append(time.monotonic())
        reports.append((done, total, message))

    async def job(ctx: JobContext) -> int:
        def work() -> int:
            calls = 0
            end = time.monotonic() + 1.25
            while time.monotonic() < end:
                calls += 1
                ctx.progress(calls, 0, "busy")
            ctx.progress(calls + 1, calls + 1, "last")
            return calls + 1

        return await ctx.run_blocking(work)

    begin = time.monotonic()
    calls = engine.submit(job, on_progress=on_progress).result(WAIT)
    elapsed = time.monotonic() - begin

    assert calls > 1000  # many calls ...
    assert reports[-1] == (calls, calls, "last")  # ... the last one is always delivered ...
    assert [r[0] for r in reports] == sorted(r[0] for r in reports)
    assert 3 <= len(reports) <= int(elapsed / 0.1) + 2  # ... and only a few in between
    throttled = stamps[:-1]  # the final flush may follow the previous delivery closely
    gaps = [later - earlier for earlier, later in zip(throttled, throttled[1:], strict=False)]
    assert min(gaps) >= 0.08, gaps
    for start in throttled:
        in_window = [stamp for stamp in throttled if start <= stamp < start + 1.0]
        assert len(in_window) <= 10, in_window


def test_progress_from_many_threads_is_delivered_one_at_a_time_on_the_engine_thread(
    engine: Engine,
) -> None:
    threads: set[str] = set()
    busy = threading.Lock()
    overlaps: list[int] = []
    count = [0]

    def on_progress(done: int, total: int, message: str) -> None:
        if not busy.acquire(blocking=False):
            overlaps.append(done)
            return
        try:
            threads.add(threading.current_thread().name)
            count[0] += 1
        finally:
            busy.release()

    async def job(ctx: JobContext) -> list[str]:
        def work() -> str:
            for step in range(2000):
                ctx.progress(step, 2000, threading.current_thread().name)
            return threading.current_thread().name

        return list(await asyncio.gather(*(ctx.run_blocking(work) for _ in range(4))))

    workers = Engine(progress_interval_s=0.0)
    workers.start()
    try:
        names = workers.submit(job, on_progress=on_progress).result(WAIT)
    finally:
        workers.shutdown()
    assert all(name.startswith("porttool-worker") for name in names)
    assert threads == {"porttool-engine"}
    assert overlaps == [] and count[0] >= 1


def test_an_exception_inside_a_progress_callback_is_logged_and_ignored(
    engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[int] = []

    def on_progress(done: int, total: int, message: str) -> None:
        calls.append(done)
        raise RuntimeError("boom in progress callback")

    async def job(ctx: JobContext) -> str:
        ctx.progress(1, 2)
        await asyncio.sleep(0.15)
        ctx.progress(2, 2)
        return "finished anyway"

    with caplog.at_level("ERROR", logger="porttool"):
        handle = engine.submit(job, on_progress=on_progress, name="noisy")
        assert handle.result(WAIT) == "finished anyway"
    assert calls == [1, 2]  # the callback is still called after it failed once
    assert caplog.text.count("progress callback of job 'noisy' failed") == 2
    assert "boom in progress callback" in caplog.text
    assert handle.state is JobState.DONE
    assert engine.submit(_answer).result(WAIT) == "ok"


# --------------------------------------------------------------------------- cancellation


def test_a_job_cancelled_while_it_runs_in_a_worker_thread(engine: Engine) -> None:
    started = threading.Event()
    stopped = threading.Event()

    async def job(ctx: JobContext) -> str:
        def work() -> str:
            started.set()
            try:
                while True:
                    ctx.check_cancelled()  # raises JobCancelled inside the worker thread
                    time.sleep(0.005)
            finally:
                stopped.set()

        return await ctx.run_blocking(work)

    handle = engine.submit(job, name="worker loop")
    assert started.wait(WAIT)
    assert handle.state is JobState.RUNNING and not handle.cancel_requested
    assert handle.cancel() is True
    assert handle.cancel_requested
    with pytest.raises(JobCancelled):
        handle.result(WAIT)
    assert stopped.is_set()  # the worker function had returned before the handle finished
    assert handle.done() and handle.cancelled() and handle.state is JobState.CANCELLED
    assert isinstance(handle.exception(), JobCancelled)
    assert handle.status().error is None
    assert handle.cancel() is False  # nothing left to cancel
    assert engine.submit(_answer).result(WAIT) == "ok"


def test_a_job_cancelled_while_awaiting(engine: Engine) -> None:
    waiting = threading.Event()
    cleaned_up = threading.Event()

    async def job(ctx: JobContext) -> str:
        try:
            waiting.set()
            await asyncio.sleep(3600)
            return "never"
        finally:
            cleaned_up.set()

    handle = engine.submit(job, name="sleeper")
    assert waiting.wait(WAIT)
    begin = time.monotonic()
    assert handle.cancel() is True
    with pytest.raises(JobCancelled):
        handle.result(WAIT)
    assert time.monotonic() - begin < 5.0
    assert cleaned_up.is_set()  # the job's own finally block ran
    assert handle.cancelled() and handle.state is JobState.CANCELLED
    assert engine.submit(_answer).result(WAIT) == "ok"


def test_a_cancelled_job_is_not_finished_before_its_worker_function_returned(
    engine: Engine,
) -> None:
    started = threading.Event()
    gate = threading.Event()

    async def job(ctx: JobContext) -> str:
        def work() -> str:
            started.set()
            gate.wait(WAIT)  # slow to notice the cancellation
            ctx.check_cancelled()
            return "not cancelled"

        return await ctx.run_blocking(work)

    handle = engine.submit(job)
    assert started.wait(WAIT)
    handle.cancel()
    time.sleep(0.2)
    assert not handle.done()  # the worker thread still runs: the job must not look finished
    assert handle.state is JobState.RUNNING and handle.cancel_requested
    gate.set()
    with pytest.raises(JobCancelled):
        handle.result(WAIT)


def test_check_cancelled_works_on_the_engine_thread_too(engine: Engine) -> None:
    proceed = threading.Event()
    reached = threading.Event()

    async def job(ctx: JobContext) -> str:
        ctx.check_cancelled()  # not cancelled yet: no exception
        reached.set()
        await ctx.run_blocking(lambda: proceed.wait(WAIT))
        ctx.check_cancelled()
        return "done"

    handle = engine.submit(job)
    assert reached.wait(WAIT)
    proceed.set()
    assert handle.result(WAIT) == "done"

    async def raises_it_itself(ctx: JobContext) -> None:
        raise JobCancelled("the job decided to stop")

    stopped = engine.submit(raises_it_itself)
    with pytest.raises(JobCancelled, match="decided to stop"):
        stopped.result(WAIT)
    assert stopped.state is JobState.CANCELLED


def test_a_job_cancelled_before_it_starts_never_runs() -> None:
    started = threading.Event()
    ran: list[str] = []

    async def blocks_the_loop(ctx: JobContext) -> None:
        started.set()
        time.sleep(0.3)  # noqa: ASYNC251 - deliberately blocks the loop to hold the next job

    async def job(ctx: JobContext) -> None:
        ran.append("job")

    with Engine() as engine:
        first = engine.submit(blocks_the_loop)
        assert started.wait(WAIT)
        handle = engine.submit(job)
        assert handle.state is JobState.QUEUED
        assert handle.cancel() is True
        with pytest.raises(JobCancelled):
            handle.result(WAIT)
        first.result(WAIT)
    assert ran == []
    assert _state(handle) is JobState.CANCELLED


def test_work_still_queued_for_the_pool_is_skipped_when_its_job_is_cancelled() -> None:
    gate = threading.Event()
    occupied = threading.Event()
    ran: list[str] = []

    async def occupies_the_only_worker(ctx: JobContext) -> str:
        def work() -> str:
            occupied.set()
            gate.wait(WAIT)
            return "first"

        return await ctx.run_blocking(work)

    async def queued(ctx: JobContext) -> None:
        await ctx.run_blocking(lambda: ran.append("queued work"))

    with Engine(max_workers=1) as engine:
        first = engine.submit(occupies_the_only_worker)
        assert occupied.wait(WAIT)
        second = engine.submit(queued)
        while second.state is JobState.QUEUED:
            time.sleep(0.005)
        second.cancel()
        gate.set()
        assert first.result(WAIT) == "first"
        with pytest.raises(JobCancelled):
            second.result(WAIT)
    assert ran == []


# --------------------------------------------------------------------------- shutdown


def test_engine_shutdown_with_running_jobs() -> None:
    before = _engine_threads()
    engine = Engine()
    engine.start()
    in_worker = threading.Event()
    awaiting = threading.Event()
    worker_stopped = threading.Event()

    async def in_a_worker(ctx: JobContext) -> None:
        def work() -> None:
            in_worker.set()
            try:
                while True:
                    ctx.check_cancelled()
                    time.sleep(0.005)
            finally:
                worker_stopped.set()

        await ctx.run_blocking(work)

    async def awaits(ctx: JobContext) -> None:
        awaiting.set()
        await asyncio.sleep(3600)

    handles = [engine.submit(in_a_worker), engine.submit(awaits)]
    handles += [engine.submit(awaits, name=f"sleeper {n}") for n in range(20)]
    assert in_worker.wait(WAIT) and awaiting.wait(WAIT)
    assert len(_engine_threads() - before) >= 2  # the engine thread and at least one worker

    begin = time.monotonic()
    assert engine.shutdown(timeout=5.0) is True
    assert time.monotonic() - begin < 5.0
    assert worker_stopped.is_set()
    for handle in handles:
        assert handle.done() and handle.cancelled()
        with pytest.raises(JobCancelled):
            handle.result(0)
    assert _engine_threads() - before == set()  # every thread was joined
    assert not engine.running
    assert engine.shutdown() is True  # a second call is harmless


def test_a_job_submitted_after_shutdown_is_refused() -> None:
    engine = Engine()
    engine.start()
    assert engine.submit(_answer).result(WAIT) == "ok"
    engine.shutdown()
    with pytest.raises(PortToolError, match="was shut down"):
        engine.submit(_answer)
    with pytest.raises(PortToolError, match="was shut down"):
        engine.run_sync(_answer)
    with pytest.raises(PortToolError, match="Create a new Engine"):
        engine.start()


def test_a_job_submitted_before_start_is_refused() -> None:
    engine = Engine()
    assert not engine.running
    with pytest.raises(PortToolError, match="not started"):
        engine.submit(_answer)
    assert engine.shutdown() is True  # shutting down an engine that never ran is fine
    with pytest.raises(PortToolError, match="Create a new Engine"):
        engine.start()


def test_start_twice_is_harmless() -> None:
    before = _engine_threads()
    engine = Engine()
    engine.start()
    engine.start()
    try:
        assert engine.running
        assert len({t for t in _engine_threads() - before if t.name == "porttool-engine"}) == 1
    finally:
        engine.shutdown()


def test_shutdown_gives_up_on_a_worker_that_ignores_cancellation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    before = _engine_threads()
    release = threading.Event()
    started = threading.Event()

    async def job(ctx: JobContext) -> str:
        def work() -> str:
            started.set()
            release.wait(WAIT)  # never looks at the cancel flag
            return "late result"

        return await ctx.run_blocking(work)

    engine = Engine()
    engine.start()
    handle = engine.submit(job, name="stubborn")
    assert started.wait(WAIT)
    try:
        with caplog.at_level("WARNING", logger="porttool"):
            begin = time.monotonic()
            clean = engine.shutdown(timeout=0.5)
            elapsed = time.monotonic() - begin
        assert clean is False
        assert 0.4 < elapsed < 3.0  # it waited for the timeout, not for the worker
        assert handle.done() and handle.cancelled()
        assert "still run after shutdown" in caplog.text
        assert len(_engine_threads() - before) == 1  # only the stuck worker is left
    finally:
        release.set()
    for thread in _engine_threads() - before:
        thread.join(WAIT)
    assert _engine_threads() - before == set()


def test_shutdown_gives_up_on_a_job_that_blocks_the_event_loop(
    caplog: pytest.LogCaptureFixture,
) -> None:
    before = _engine_threads()
    started = threading.Event()

    async def job(ctx: JobContext) -> str:
        started.set()
        time.sleep(1.0)  # noqa: ASYNC251 - a faulty job that blocks the event loop
        return "late"

    engine = Engine()
    engine.start()
    handle = engine.submit(job, name="blocker")
    assert started.wait(WAIT)
    with caplog.at_level("WARNING", logger="porttool"):
        begin = time.monotonic()
        clean = engine.shutdown(timeout=0.2)
        elapsed = time.monotonic() - begin
    assert clean is False and elapsed < 0.9
    assert "blocking the event loop" in caplog.text
    with pytest.raises(JobCancelled, match="shut down before the job stopped"):
        handle.result(0)
    for thread in _engine_threads() - before:
        thread.join(WAIT)
    assert _engine_threads() - before == set()
    assert handle.cancelled()  # the late result did not replace the outcome


def test_engine_as_context_manager() -> None:
    before = _engine_threads()
    with Engine() as engine:
        assert engine.running
        assert engine.run_sync(_answer, name="inline") == "ok"
    assert not engine.running
    assert _engine_threads() - before == set()


# --------------------------------------------------------------------------- run_sync


def test_run_sync_runs_one_job_and_cleans_up() -> None:
    before = _engine_threads()
    reports: list[tuple[int, int, str]] = []

    async def job(ctx: JobContext) -> str:
        ctx.progress(1, 1, "all")
        return await ctx.run_blocking(lambda: "from the pool")

    result = run_sync(job, on_progress=lambda d, t, m: reports.append((d, t, m)), name="cli job")
    assert result == "from the pool"
    assert reports == [(1, 1, "all")]
    assert _engine_threads() - before == set()


def test_run_sync_raises_the_job_error_and_cleans_up() -> None:
    before = _engine_threads()

    async def job(ctx: JobContext) -> None:
        raise PortToolError("nothing to do")

    with pytest.raises(PortToolError, match="nothing to do"):
        run_sync(job)
    assert _engine_threads() - before == set()


def test_ctrl_c_during_run_sync_cancels_the_job(monkeypatch: pytest.MonkeyPatch) -> None:
    cancelled = threading.Event()
    real_wait = concurrent.futures.wait
    calls = [0]

    def interrupted_wait(*args: object, **kwargs: object) -> object:
        calls[0] += 1
        if calls[0] == 2:
            raise KeyboardInterrupt
        return real_wait(*args, **kwargs)  # type: ignore[arg-type, unused-ignore]

    async def job(ctx: JobContext) -> None:
        try:
            await asyncio.sleep(3600)
        finally:
            cancelled.set()

    monkeypatch.setattr(concurrent.futures, "wait", interrupted_wait)
    with pytest.raises(KeyboardInterrupt):
        run_sync(job)
    assert cancelled.wait(WAIT)


# --------------------------------------------------------------------------- start-up failure


def test_engine_start_failure_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_loop() -> asyncio.AbstractEventLoop:
        raise OSError("no event loop today")

    monkeypatch.setattr(asyncio, "new_event_loop", no_loop)
    engine = Engine()
    with pytest.raises(PortToolError, match="could not be started"):
        engine.start()
    assert not engine.running
    with pytest.raises(PortToolError):
        engine.submit(_answer)


def test_a_crashing_event_loop_fails_its_jobs_instead_of_hanging(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = Engine()
    engine.start()
    waiting = threading.Event()

    async def job(ctx: JobContext) -> None:
        waiting.set()
        await asyncio.sleep(3600)

    def crash() -> None:
        raise SystemExit("a callback ends the loop")

    handle = engine.submit(job)
    assert waiting.wait(WAIT)
    with caplog.at_level("ERROR", logger="porttool"):
        assert engine._call_on_loop(crash)
        with pytest.raises(JobCancelled):
            handle.result(WAIT)
    with pytest.raises(PortToolError, match="was shut down"):
        engine.submit(_answer)
    engine.shutdown()
    assert "stopped on an unexpected error" in caplog.text


# --------------------------------------------------------------------------- dependencies


def test_the_engine_imports_no_gui_toolkit() -> None:
    script = (
        "import sys\n"
        "import porttool.engine\n"
        "gui = {'PySide6', 'PySide2', 'PyQt5', 'PyQt6', 'tkinter', 'wx'}\n"
        "print(sorted(name for name in sys.modules if name.split('.')[0] in gui))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", script], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "[]"
