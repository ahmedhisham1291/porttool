# HANDOFF: porttool/engine (Agent 1, Foundation)

## Done
- `Engine`: one engine thread running one asyncio loop created with
  `asyncio.new_event_loop()` (no platform branch), plus one `ThreadPoolExecutor` with
  `min(32, os.cpu_count() + 4)` workers.
- Jobs with progress, cancellation, error capture, thread-safe handles, a job registry
  for polling clients, `shutdown(timeout)` and `run_sync`.
- The engine imports no GUI toolkit (checked by a test in a fresh interpreter). Runtime
  imports: standard library and `porttool.core` only.

## Not done
- Nothing from the task list is missing.
- No process pool. The shared context allows one only after a benchmark proves the gain.
- No "run DaVinci tools one at a time" lock: that belongs to porttool/davinci (create an
  `asyncio.Lock` inside a job; all jobs share the one loop).

## Public API (`from porttool.engine import ...`)
```python
engine = Engine(max_workers=None, progress_interval_s=0.1, keep_finished=200)
engine.start()                      # idempotent; PortToolError after shutdown
handle = engine.submit(job_fn, on_progress=None, name="")   # any thread
engine.run_sync(job_fn, on_progress=None, name="")           # submit + wait; Ctrl+C cancels
engine.get(job_id) -> JobHandle | None       # lookup by id (unfinished + newest 200 finished)
engine.jobs() -> tuple[JobHandle, ...]
engine.shutdown(timeout=5.0) -> bool         # True = everything stopped; idempotent
engine.running, engine.max_workers
with Engine() as engine: ...                 # start / shutdown

run_sync(job_fn, on_progress=None, name="", shutdown_timeout=5.0)   # private engine, for CLI
```
A job is `async def job(ctx: JobContext) -> T`. `ctx` implements
`porttool.core.interfaces.JobContext` exactly:
- `ctx.progress(done, total, message="")`: any thread, cheap, throttled.
- `ctx.check_cancelled()`: raises `JobCancelled`; any thread.
- `await ctx.run_blocking(fn)`: runs `fn()` in the pool.

`JobHandle[T]` (every member is safe from any thread, e.g. an HTTP handler):
| member | meaning |
| --- | --- |
| `id` | unique string (uuid hex) |
| `name` | given name, default the function name |
| `state` | `JobState`: `"queued"`, `"running"`, `"done"`, `"failed"`, `"cancelled"` |
| `progress` | latest `(done, total, message)` the job reported (not throttled) or `None` |
| `cancel_requested` | `cancel()` was called |
| `status()` | `JobStatus(id, name, state, progress, cancel_requested, error)`: one snapshot; `error` is text, set only for `"failed"` |
| `done()`, `cancelled()` | finished / finished as cancelled |
| `result(timeout=None)` | result, or raises the job's exception, `JobCancelled`, `TimeoutError` (`timeout=0` polls) |
| `exception(timeout=None)` | the exception (`JobCancelled` if cancelled) or `None` |
| `cancel()` | request cancellation; `False` if already finished; returns at once |
| `future` | the `concurrent.futures.Future` behind it (read only) |
| `add_done_callback(fn)` | `fn(handle)` on the engine thread, or at once if already finished |

## Behaviour you can rely on (each has a named test)
- **Progress**: at most one delivery per 0.1 s per job (ten per second). The first report
  goes out at once, the newest report replaces older undelivered ones, and the last
  report of a job is always delivered before the handle finishes. `on_progress` runs on
  the **engine thread**, never concurrently: keep it short, never block in it. A web or
  GUI layer should not need it: poll `handle.progress`. An exception in the callback is
  logged and ignored.
- **A job that raises**: logged once with traceback (logger `porttool.engine.engine`),
  re-raised by `handle.result()`, state `"failed"`. The loop keeps running. `sys.exit()`
  or `KeyboardInterrupt` inside a job (loop or worker) becomes a `PortToolError`.
- **Cancel**: sets a `threading.Event` (seen by `check_cancelled()` in workers) and cancels
  the asyncio task (interrupts an `await`). The handle ends with `JobCancelled`.
  A job waiting in `run_blocking` is **not finished until its worker function has
  returned**, so no worker thread outlives its job. Work still queued for the pool is
  skipped. Inside a job, cancellation shows up as `asyncio.CancelledError` at an `await`
  (or `JobCancelled` from `run_blocking` if the worker noticed first): use `try/finally`
  for clean-up, do not swallow either.
- **Shutdown**: refuses new jobs, cancels all jobs, waits up to `timeout`, stops the loop,
  joins the engine thread and the workers. If a worker function ignores cancellation or
  a job blocks the loop, it returns `False` after about `timeout` seconds, logs which
  part is stuck, and finishes the affected handles with `JobCancelled`.
- **Errors by design**: `submit` before `start` or after `shutdown` raises `PortToolError`;
  `handle.result()`, `engine.run_sync()` and `engine.shutdown()` called from inside a job
  raise `PortToolError` instead of deadlocking.
- 200 jobs submitted at once all finish with their own results and ids.

## Limits and things to know
- Threads cannot be killed. A worker function that never calls `ctx.check_cancelled()`
  keeps its thread until it returns; Python joins pool threads at interpreter exit, so a
  worker stuck forever also delays process exit. Call `check_cancelled()` in every loop.
- The engine thread is a daemon thread; the worker threads are not.
- Never do CPU work or blocking calls in the job coroutine itself: it blocks every job.
- Finished handles stay in `engine.get()` only for the newest 200 (`keep_finished`).

## How to run the tests
```
python3 -m pytest tests/unit/engine -q
mypy --strict porttool/engine tests/unit/engine
ruff check porttool/engine tests/unit/engine
```
Tails (this sandbox, Python 3.13):
```
38 passed in 4.48s
Success: no issues found in 4 source files
All checks passed!
```
The engine tests were repeated 5 times with `-W error` and 3 times under artificial CPU
load without a failure.

## Measured (2 CPU cores shared with other agents: treat as rough)
- Engine test file: 4.5 s. 200 jobs with one `run_blocking` each: well under 1 s.
- Coverage, measured once with the stdlib (`python3 -m trace --count --missing`, not
  coverage.py): `porttool/engine/engine.py` 511 lines executed, 20 missed = 96.2 %.

## Unverified on Windows
- That `asyncio.new_event_loop()` in the engine thread is a Proactor loop and that
  `asyncio.create_subprocess_exec` works from it. Tested here only on Linux's loop
  (`test_the_engine_thread_can_run_subprocesses`, plus one test with the fake DaVinci tool).
- Ctrl+C during `run_sync`: the wait runs in 0.2 s slices so that Windows can deliver it;
  tested here only by simulating the `KeyboardInterrupt`.
- Progress throttling with the 15.6 ms Windows timer: the code re-checks the clock when
  a timer fires early, so the ten-per-second limit should hold, but the timing test ran
  on Linux only.

## PENDING-DISCOVERY
None in the engine.

## Contract change requests
None. `JobContext` was implemented as written.

## Open questions
- Should `JobStatus` carry start/end times or elapsed seconds for the web interface?
  Not added because nothing asked for it.
