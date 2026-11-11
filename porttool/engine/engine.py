"""The background engine.

Threads:

* the **engine thread** runs one asyncio event loop that orchestrates every job;
* the **worker threads** of one ``ThreadPoolExecutor`` run blocking and CPU-heavy work
  (``JobContext.run_blocking``);
* any other thread (the command line, an HTTP handler) calls :meth:`Engine.submit` and
  reads the :class:`JobHandle`. Every method of ``Engine`` and ``JobHandle`` is thread-safe.

A job is ``async def job(ctx: JobContext) -> T``. Its coroutine runs on the engine
thread: it must only await and hand real work to ``ctx.run_blocking``.

Cancellation: ``JobHandle.cancel()`` sets a flag that worker code sees through
``ctx.check_cancelled()`` and cancels the job's asyncio task. A job that waits in
``run_blocking`` is not finished before its worker function has returned, so no worker
thread outlives its job (except when ``Engine.shutdown`` gives up after its timeout).
A cancelled job ends with ``JobCancelled`` from ``JobHandle.result()``.

The engine imports no GUI toolkit and never touches one.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import functools
import logging
import os
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Coroutine
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum, StrEnum
from types import TracebackType
from typing import Any, Generic, TypeVar

from porttool.core.errors import JobCancelled, PortToolError
from porttool.core.interfaces import JobContext
from porttool.core.log import loop_exception_handler

_logger = logging.getLogger(__name__)

_T = TypeVar("_T")
_R = TypeVar("_R")

#: A job function: ``async def job(ctx: JobContext) -> T``.
JobFn = Callable[[JobContext], Coroutine[Any, Any, _T]]
#: ``on_progress(done, total, message)``. Called on the engine thread, never concurrently.
ProgressCallback = Callable[[int, int, str], None]

#: Minimum time between two progress deliveries of one job: at most ten per second.
PROGRESS_INTERVAL_S = 0.1

_START_TIMEOUT_S = 10.0
_DRAIN_GRACE_S = 0.05
_MIN_JOIN_S = 0.05
_POLL_S = 0.2


class JobState(StrEnum):
    QUEUED = "queued"  # submitted, the engine thread has not started it yet
    RUNNING = "running"
    DONE = "done"  # finished with a result
    FAILED = "failed"  # finished with an exception
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class JobStatus:
    """One consistent snapshot of a job, ready to be sent to a client."""

    id: str
    name: str
    state: JobState
    progress: tuple[int, int, str] | None  # latest (done, total, message), None before the first
    cancel_requested: bool
    error: str | None  # the failure as text when state is "failed", else None


class _EngineState(Enum):
    NEW = 1
    RUNNING = 2
    CLOSED = 3


def _pool_size() -> int:
    return min(32, (os.cpu_count() or 1) + 4)


def _transfer(worker: Future[Any], waiter: asyncio.Future[Any]) -> None:
    """Copy the outcome of a worker-pool future to an asyncio future (engine thread)."""
    if waiter.done():
        return
    if worker.cancelled():
        waiter.cancel()
        return
    error = worker.exception()
    if error is None:
        waiter.set_result(worker.result())
    elif isinstance(error, StopIteration):
        # asyncio futures refuse StopIteration; it must not get lost either.
        waiter.set_exception(RuntimeError(f"worker function raised StopIteration: {error!r}"))
    else:
        waiter.set_exception(error)


class _Context:
    """The ``JobContext`` of one job."""

    def __init__(
        self, job: _Job[Any], on_progress: ProgressCallback | None, interval_s: float
    ) -> None:
        self._job = job
        self._engine = job.engine
        self._on_progress = on_progress
        self._interval_s = interval_s
        self._lock = threading.Lock()
        self._latest: tuple[int, int, str] | None = None  # the newest report, read by the handle
        self._undelivered: tuple[int, int, str] | None = None
        self._pump_scheduled = False
        self._closed = False
        self._next_delivery = 0.0  # time.monotonic() before which nothing is delivered
        self._timer: asyncio.TimerHandle | None = None

    # ---- JobContext

    def progress(self, done: int, total: int, message: str = "") -> None:
        report = (done, total, message)
        with self._lock:
            if self._closed:
                return
            self._latest = report
            if self._on_progress is None:
                return
            self._undelivered = report
            if self._pump_scheduled:
                return
            self._pump_scheduled = True
        self._engine._call_on_loop(self._pump)

    def check_cancelled(self) -> None:
        if self._job.cancel_event.is_set():
            raise JobCancelled()

    async def run_blocking(self, fn: Callable[[], _R]) -> _R:
        self.check_cancelled()
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[_R] = loop.create_future()

        def call() -> _R:
            self.check_cancelled()  # work that was still queued when the job was cancelled
            return fn()

        worker = self._engine._submit_to_pool(call)
        worker.add_done_callback(functools.partial(self._engine._deliver_worker_result, waiter))
        interrupted = False
        while True:
            try:
                result = await asyncio.shield(waiter)
            except asyncio.CancelledError:
                if waiter.done() or self._engine._abandoning:
                    # Either the worker never ran (pool shut down) or the engine gives up
                    # waiting for a worker that ignores the cancel flag.
                    if waiter.done() and not waiter.cancelled():
                        waiter.exception()  # mark as retrieved
                    raise
                # The job was cancelled while the worker function runs. A thread cannot
                # be killed: keep waiting until the function returns, then cancel.
                interrupted = True
                continue
            except BaseException as error:
                if not interrupted:
                    raise
                if not isinstance(error, JobCancelled):
                    _logger.info(
                        "worker function of cancelled job %r ended with %r",
                        self._job.name,
                        error,
                    )
                raise asyncio.CancelledError() from None
            if interrupted:
                raise asyncio.CancelledError()
            return result

    # ---- used by the engine and the handle

    @property
    def latest(self) -> tuple[int, int, str] | None:
        with self._lock:
            return self._latest

    def _pump(self) -> None:
        """Engine thread: deliver now, or arm a timer for the rest of the interval."""
        self._timer = None
        wait_s = self._next_delivery - time.monotonic()
        if wait_s > 0:
            # Timers may fire a little early (clock resolution): _pump checks again.
            self._timer = self._engine._loop_or_raise().call_later(wait_s, self._pump)
            return
        self._deliver(final=False)

    def _deliver(self, *, final: bool) -> None:
        with self._lock:
            report, self._undelivered = self._undelivered, None
            self._pump_scheduled = False
            if final:
                self._closed = True
        if report is None or self._on_progress is None:
            return
        self._next_delivery = time.monotonic() + self._interval_s
        try:
            self._on_progress(*report)
        except Exception:  # boundary: a caller's callback must not break the job
            _logger.exception(
                "progress callback of job %r failed; the job continues", self._job.name
            )

    def close(self, *, deliver: bool) -> None:
        """The job is over: deliver the last report (regardless of the throttle), then go quiet."""
        if deliver:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._deliver(final=True)
        else:
            with self._lock:
                self._closed = True


class _Job(Generic[_T]):
    """Internal state of one job. The public face is :class:`JobHandle`."""

    def __init__(
        self,
        engine: Engine,
        fn: JobFn[_T],
        name: str,
        job_id: str,
        on_progress: ProgressCallback | None,
    ) -> None:
        self.engine = engine
        self.fn = fn
        self.name = name
        self.id = job_id
        # The future is the single source of truth for "finished". It is marked running
        # at once so that only the engine can complete it (future.cancel() returns False).
        self.future: Future[_T] = Future()
        self.future.set_running_or_notify_cancel()
        self.cancel_event = threading.Event()
        self.started = False
        self.task: asyncio.Task[_T] | None = None
        self.context = _Context(self, on_progress, engine._progress_interval_s)
        self.handle: JobHandle[_T] = JobHandle(self)

    def cancel_task(self) -> None:
        """Engine thread."""
        if self.task is not None and not self.task.done():
            self.task.cancel()


class JobHandle(Generic[_T]):
    """What :meth:`Engine.submit` returns. Every member is safe to use from any thread."""

    def __init__(self, job: _Job[_T]) -> None:
        self._job = job

    @property
    def id(self) -> str:
        """Unique id of the job, see :meth:`Engine.get`."""
        return self._job.id

    @property
    def name(self) -> str:
        return self._job.name

    @property
    def future(self) -> Future[_T]:
        """The ``concurrent.futures.Future`` behind this handle. Do not complete it yourself."""
        return self._job.future

    @property
    def state(self) -> JobState:
        future = self._job.future
        if not future.done():
            return JobState.RUNNING if self._job.started else JobState.QUEUED
        error = future.exception()
        if error is None:
            return JobState.DONE
        return JobState.CANCELLED if isinstance(error, JobCancelled) else JobState.FAILED

    @property
    def progress(self) -> tuple[int, int, str] | None:
        """The latest ``(done, total, message)`` the job reported (not throttled), or None."""
        return self._job.context.latest

    @property
    def cancel_requested(self) -> bool:
        return self._job.cancel_event.is_set()

    def status(self) -> JobStatus:
        state = self.state
        error: str | None = None
        if state is JobState.FAILED:
            failure = self._job.future.exception()
            if isinstance(failure, PortToolError):
                error = str(failure)
            else:
                error = f"{type(failure).__name__}: {failure}"
        return JobStatus(
            id=self.id,
            name=self.name,
            state=state,
            progress=self.progress,
            cancel_requested=self.cancel_requested,
            error=error,
        )

    def done(self) -> bool:
        """True once the job has finished: with a result, an exception or cancelled."""
        return self._job.future.done()

    def cancelled(self) -> bool:
        return self.state is JobState.CANCELLED

    def cancel(self) -> bool:
        """Ask the job to stop. Returns False if it has already finished.

        Returns at once. The job ends when its worker code reaches ``check_cancelled()``
        or its coroutine reaches the next ``await``; then ``result()`` raises ``JobCancelled``.
        """
        return self._job.engine._cancel(self._job)

    def result(self, timeout: float | None = None) -> _T:
        """Wait for the job and return its result.

        Raises the job's exception if it failed, ``JobCancelled`` if it was cancelled and
        ``TimeoutError`` if it is not finished after ``timeout`` seconds (``0`` polls).
        """
        self._refuse_deadlock()
        return self._job.future.result(timeout)

    def exception(self, timeout: float | None = None) -> BaseException | None:
        """Wait for the job and return its exception (``JobCancelled`` if cancelled) or None."""
        self._refuse_deadlock()
        return self._job.future.exception(timeout)

    def add_done_callback(self, fn: Callable[[JobHandle[_T]], None]) -> None:
        """Call ``fn(handle)`` when the job has finished: on the engine thread, or at once
        on the calling thread if it already has. An exception in ``fn`` is logged."""

        def call(_future: Future[_T]) -> None:
            try:
                fn(self)
            except Exception:  # boundary: a caller's callback must not break the engine
                _logger.exception("done callback of job %r failed", self.name)

        self._job.future.add_done_callback(call)

    def _refuse_deadlock(self) -> None:
        if not self._job.future.done() and self._job.engine._on_engine_thread():
            raise PortToolError(
                f"Waiting for job {self.name!r} on the engine thread would block the engine "
                "forever. Inside a job, await the work directly instead of submitting a job."
            )

    def __repr__(self) -> str:
        return f"<JobHandle {self.id} {self.name!r} {self.state.value}>"


class Engine:
    """One event loop thread plus one worker thread pool. See the module docstring.

    ::

        engine = Engine()
        engine.start()
        handle = engine.submit(job, on_progress=print, name="load repo")
        model = handle.result()
        engine.shutdown()
    """

    def __init__(
        self,
        *,
        max_workers: int | None = None,
        progress_interval_s: float = PROGRESS_INTERVAL_S,
        keep_finished: int = 200,
    ) -> None:
        self._max_workers = max_workers if max_workers is not None else _pool_size()
        self._progress_interval_s = progress_interval_s
        self._keep_finished = keep_finished
        self._lock = threading.Lock()
        self._state = _EngineState.NEW
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._pool: ThreadPoolExecutor | None = None
        self._ready = threading.Event()
        self._start_error: BaseException | None = None
        self._active: set[_Job[Any]] = set()
        self._handles: OrderedDict[str, JobHandle[Any]] = OrderedDict()
        self._finished_ids: list[str] = []
        self._workers: set[threading.Thread] = set()
        self._abandoning = False
        self._clean_shutdown = True

    # ---- life cycle

    @property
    def max_workers(self) -> int:
        return self._max_workers

    @property
    def running(self) -> bool:
        with self._lock:
            return self._state is _EngineState.RUNNING

    def start(self) -> None:
        """Start the engine thread and the worker pool. Calling it twice is harmless."""
        with self._lock:
            if self._state is _EngineState.RUNNING:
                return
            if self._state is _EngineState.CLOSED:
                raise PortToolError("This engine was shut down. Create a new Engine.")
            self._pool = ThreadPoolExecutor(
                max_workers=self._max_workers,
                thread_name_prefix="porttool-worker",
                initializer=self._register_worker,
            )
            # Daemon: a forgotten shutdown() must not keep the process alive.
            self._thread = threading.Thread(
                target=self._run_loop, name="porttool-engine", daemon=True
            )
            self._thread.start()
            if not self._ready.wait(_START_TIMEOUT_S) or self._start_error is not None:
                self._state = _EngineState.CLOSED
                self._pool.shutdown(wait=False)
                raise PortToolError(
                    "The background engine could not be started.",
                    detail=repr(self._start_error),
                ) from self._start_error
            self._state = _EngineState.RUNNING

    def shutdown(self, timeout: float = 5.0) -> bool:
        """Cancel running jobs, stop the loop and join all threads.

        Waits at most about ``timeout`` seconds in total. Returns True when everything
        stopped. Returns False (and logs which part is stuck) when a job blocks the event
        loop or a worker function ignores cancellation: its handle then ends with
        ``JobCancelled`` although the thread still runs. Calling it again is harmless.
        """
        if self._on_engine_thread():
            raise PortToolError("Engine.shutdown() must not be called from inside a job.")
        with self._lock:
            previous = self._state
            self._state = _EngineState.CLOSED
            jobs = list(self._active)
            thread, pool = self._thread, self._pool
        if previous is not _EngineState.RUNNING or thread is None or pool is None:
            return self._clean_shutdown
        deadline = time.monotonic() + timeout

        def remaining() -> float:
            return max(deadline - time.monotonic(), _MIN_JOIN_S)

        for job in jobs:
            self._cancel(job)
        concurrent.futures.wait([job.future for job in jobs], timeout=remaining())

        loop = self._loop
        if loop is not None:
            self._call_on_loop(loop.stop)
        thread.join(remaining())
        clean = not thread.is_alive()
        if not clean:
            _logger.warning(
                "the engine thread did not stop within %.1f s: a job is blocking the event loop",
                timeout,
            )
        self._abandon_unfinished("the engine was shut down before the job stopped")

        pool.shutdown(wait=False, cancel_futures=True)
        me = threading.current_thread()
        with self._lock:
            workers = [worker for worker in self._workers if worker is not me]
        for worker in workers:
            worker.join(remaining())
        stuck = sorted(worker.name for worker in workers if worker.is_alive())
        if stuck:
            clean = False
            _logger.warning(
                "%d worker thread(s) still run after shutdown (%s): their work ignores "
                "cancellation",
                len(stuck),
                ", ".join(stuck),
            )
        self._clean_shutdown = clean
        return clean

    def __enter__(self) -> Engine:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.shutdown()

    # ---- jobs

    def submit(
        self,
        job_fn: JobFn[_T],
        *,
        on_progress: ProgressCallback | None = None,
        name: str = "",
    ) -> JobHandle[_T]:
        """Queue ``job_fn`` and return its handle at once. Thread-safe.

        ``on_progress(done, total, message)`` is called on the engine thread, at most ten
        times per second; the last report of a job is always delivered. Keep it short and
        do not block in it. Raises ``PortToolError`` before ``start()`` and after ``shutdown()``.
        """
        job_id = uuid.uuid4().hex
        job: _Job[_T] = _Job(
            self, job_fn, name or getattr(job_fn, "__name__", "job"), job_id, on_progress
        )
        with self._lock:
            if self._state is _EngineState.NEW:
                raise PortToolError("The engine is not started. Call Engine.start() first.")
            if self._state is _EngineState.CLOSED:
                raise PortToolError("The engine was shut down. No new job can be started.")
            self._active.add(job)
            self._handles[job_id] = job.handle
            # Inside the lock: shutdown() cannot stop the loop between these two steps.
            scheduled = self._call_on_loop(self._spawn, job)
        if not scheduled:
            self._finish(job, error=JobCancelled("The engine stopped before the job started."))
        return job.handle

    def run_sync(
        self,
        job_fn: JobFn[_T],
        *,
        on_progress: ProgressCallback | None = None,
        name: str = "",
    ) -> _T:
        """Submit a job and wait for its result. Ctrl+C cancels the job."""
        if self._on_engine_thread():
            raise PortToolError(
                "Engine.run_sync() must not be called from inside a job: it would block "
                "the engine forever."
            )
        handle = self.submit(job_fn, on_progress=on_progress, name=name)
        try:
            # Wait in short slices: on Windows an unbounded wait cannot be interrupted by Ctrl+C.
            while not handle.done():
                concurrent.futures.wait([handle.future], timeout=_POLL_S)
        except KeyboardInterrupt:
            handle.cancel()
            raise
        return handle.result()

    def get(self, job_id: str) -> JobHandle[Any] | None:
        """Look a job up by ``JobHandle.id``. Finished jobs stay available (the newest 200)."""
        with self._lock:
            return self._handles.get(job_id)

    def jobs(self) -> tuple[JobHandle[Any], ...]:
        """All known jobs, oldest first: every unfinished one and the newest finished ones."""
        with self._lock:
            return tuple(self._handles.values())

    # ---- internals: any thread

    def _on_engine_thread(self) -> bool:
        return self._thread is not None and threading.current_thread() is self._thread

    def _loop_or_raise(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            raise PortToolError("The engine has no event loop.")
        return self._loop

    def _call_on_loop(self, callback: Callable[..., object], *args: object) -> bool:
        """Schedule ``callback`` on the engine thread. False if the loop is gone."""
        loop = self._loop
        if loop is None:
            return False
        try:
            loop.call_soon_threadsafe(callback, *args)
        except RuntimeError:  # "Event loop is closed": the engine was shut down
            return False
        return True

    def _register_worker(self) -> None:
        with self._lock:
            self._workers.add(threading.current_thread())

    def _submit_to_pool(self, call: Callable[[], _R]) -> Future[_R]:
        pool = self._pool
        if pool is None:
            raise PortToolError("The engine has no worker pool.")
        try:
            return pool.submit(call)
        except RuntimeError as error:  # the pool was shut down
            raise JobCancelled("The engine is shutting down.") from error

    def _deliver_worker_result(self, waiter: asyncio.Future[Any], worker: Future[Any]) -> None:
        """Worker thread: hand the outcome of a worker function to the waiting job."""
        if not self._call_on_loop(_transfer, worker, waiter) and not worker.cancelled():
            _logger.warning(
                "a worker function finished after the engine was shut down: result dropped"
            )

    def _cancel(self, job: _Job[Any]) -> bool:
        if job.future.done():
            return False
        job.cancel_event.set()
        self._call_on_loop(job.cancel_task)
        return True

    def _finish(
        self,
        job: _Job[Any],
        *,
        result: object = None,
        error: BaseException | None = None,
        deliver: bool = True,
    ) -> None:
        """Complete a job's handle exactly once and forget the job."""
        if job.future.done():
            return
        job.context.close(deliver=deliver)
        with self._lock:
            self._active.discard(job)
            self._finished_ids.append(job.id)
            while len(self._finished_ids) > self._keep_finished:
                self._handles.pop(self._finished_ids.pop(0), None)
        try:
            if error is None:
                _logger.debug("job %r (%s) finished", job.name, job.id)
                job.future.set_result(result)
            elif isinstance(error, JobCancelled):
                _logger.info("job %r (%s) was cancelled", job.name, job.id)
                job.future.set_exception(error)
            else:
                _logger.error(
                    "job %r (%s) failed: %s",
                    job.name,
                    job.id,
                    error,
                    exc_info=(type(error), error, error.__traceback__),
                )
                job.future.set_exception(error)
        except concurrent.futures.InvalidStateError:
            # Only possible when shutdown() gave up on a blocked engine thread and both
            # threads finish the same job. The first outcome stands.
            _logger.debug("job %r (%s) was already finished", job.name, job.id)

    def _abandon_unfinished(self, reason: str) -> None:
        with self._lock:
            leftover = list(self._active)
        for job in leftover:
            _logger.warning("job %r (%s) abandoned: %s", job.name, job.id, reason)
            self._finish(job, error=JobCancelled(f"The job was stopped: {reason}."), deliver=False)

    # ---- internals: engine thread

    def _run_loop(self) -> None:
        try:
            # On Windows this is the Proactor loop, which asyncio subprocesses need.
            loop = asyncio.new_event_loop()
        except Exception as error:  # boundary: thread entry
            _logger.exception("the engine event loop could not be created")
            self._start_error = error
            self._ready.set()
            return
        self._loop = loop
        asyncio.set_event_loop(loop)
        loop.set_exception_handler(loop_exception_handler)
        loop.call_soon(self._ready.set)
        try:
            loop.run_forever()
        except BaseException:  # boundary: thread entry; nothing may end this thread silently
            _logger.exception("the engine event loop stopped on an unexpected error")
        finally:
            with self._lock:
                self._state = _EngineState.CLOSED
            try:
                self._drain(loop)
            except Exception:  # boundary: thread exit
                _logger.exception("the engine event loop did not shut down cleanly")
            finally:
                self._abandon_unfinished("the engine stopped")
                asyncio.set_event_loop(None)
                loop.close()

    def _drain(self, loop: asyncio.AbstractEventLoop) -> None:
        """After the loop stopped: give cancelled tasks one last moment, then drop them."""
        self._abandoning = True
        pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.wait(pending, timeout=_DRAIN_GRACE_S))
        loop.run_until_complete(loop.shutdown_asyncgens())

    def _spawn(self, job: _Job[Any]) -> None:
        if job.cancel_event.is_set():
            self._finish(job, error=JobCancelled())
            return
        job.task = self._loop_or_raise().create_task(self._run_job(job), name=job.name)
        job.task.add_done_callback(functools.partial(self._on_task_done, job))

    async def _run_job(self, job: _Job[_T]) -> _T:
        job.started = True
        try:
            return await job.fn(job.context)
        except (KeyboardInterrupt, SystemExit) as error:
            # These would end the event loop. A job must never stop the program.
            raise PortToolError(
                f"Job {job.name!r} tried to stop the program ({type(error).__name__})."
            ) from error

    def _on_task_done(self, job: _Job[Any], task: asyncio.Task[Any]) -> None:
        if task.cancelled():
            self._finish(job, error=JobCancelled())
            return
        error = task.exception()
        if error is None:
            self._finish(job, result=task.result())
        else:
            self._finish(job, error=error)


def run_sync(
    job_fn: JobFn[_T],
    *,
    on_progress: ProgressCallback | None = None,
    name: str = "",
    shutdown_timeout: float = 5.0,
) -> _T:
    """Run one job on a private engine and return its result. For command-line use.

    Starts an engine, runs the job, shuts the engine down. Ctrl+C cancels the job.
    """
    engine = Engine()
    engine.start()
    try:
        return engine.run_sync(job_fn, on_progress=on_progress, name=name)
    finally:
        engine.shutdown(shutdown_timeout)
