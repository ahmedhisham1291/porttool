# Shared context for every agent

## Project
PortTool is a Windows desktop tool for an AUTOSAR Classic 4.x ECU project built with
Vector MICROSAR, DaVinci Developer Classic and DaVinci Configurator 5 (.dpa project
file, DVCfgCmd command line).

Today an engineer opens DaVinci Developer, creates data types, port interfaces and port
prototypes, sets runnable access (implicit or explicit) and triggers, connects the port
to another SWC, then opens DaVinci Configurator and generates RTE and OS. PortTool
produces the same result without opening either GUI:

1. Read the repo and show every SWC with its ports, interfaces, data types, runnable
   access, triggers and connections.
2. Export an Excel workbook in which several teams enter change requests.
3. Import the workbook, validate every request, show a dry run.
4. Apply accepted requests by editing the DaVinci Developer workspace ARXML files
   directly, on a new git branch.
5. Check the workspace with DVWspChecker. Use DVCfgCmd to map new triggers to tasks,
   apply solving actions, and generate RTE and OS.
6. Route requests on model-owned SWCs (Simulink, TargetLink) to their owners and hold
   dependent connections.
7. Report the result of every request.

## Platform
Windows 10 and 11, 64-bit only. Python 3.11 or newer, 64-bit. Write no macOS or Linux
code paths and no platform abstraction layers.

## Hard constraints
- The DaVinci Developer workspace (.dcf plus its ARXML files) is the single source of
  truth. Excel is a request form. The flat extract is a read-only view.
- Never edit FlatExtract, FlatMap, GenData or any generated file.
- Never hand-edit the ECUC file. Changes to the Configurator project go through
  DVCfgCmd and a Groovy script task.
- Never automate the DaVinci GUIs. Use only DVWspChecker, DVCfgCmd, DVSwcGen, and
  DVImEx as the fallback write path.
- Do not invent CLI flags or automation API calls. Use docs/discovery.md and docs/cli/
  written by Agent 0. If the fact you need is missing, stop and ask.
- Every direct edit must look like an edit DaVinci Developer would have saved. The
  golden diffs in docs/discovery.md are the reference.
- Never write into a model-owned SWC.
- Every write to the ECU repo happens on a new git branch. Refuse to run on a dirty
  working tree. The default mode is dry run.
- No network access and no telemetry. Never copy real project data into this repo, its
  tests, logs or your messages.

## Architecture (one package per agent)
| Package | Content | Owner |
| --- | --- | --- |
| porttool/core | contracts: models, operations, results, errors, config, logging, file utilities | Agent 1 |
| porttool/engine | background engine: asyncio loop, thread pool, jobs, progress, cancellation | Agent 1 |
| porttool/reader | ARXML and ECUC loading, cache, model | Agent 2 |
| porttool/workbook | Excel export and import | Agent 3 |
| porttool/validator | rules, routing, change set, pending queue | Agent 4 |
| porttool/writer | in-place ARXML edits, delta ARXML | Agent 5 |
| porttool/davinci | Vector CLI adapters, Groovy script task | Agent 6 |
| porttool/pipeline | apply transaction, git, recovery, report | Agent 7 |
| porttool/gui | local web interface: HTTP server on 127.0.0.1 plus HTML/JS, opened in the default browser | Agent 8 |
| porttool/discovery | `porttool discover`: self-service study of the real repo on the Windows laptop | Agent 0 |
| tests/integration, tests/bench, tests/soak | cross-package tests and benchmarks | Agent 9 |
| packaging, docs/user | build, package, user guide | Agent 10 |

Packages talk only through the contracts in porttool/core. No package imports another
package's internals. Each package exposes `register_cli(group)` for a click group, so every feature is also
a command-line command. The CLI uses click (not Typer).

## Concurrency model (mandatory)
- Three kinds of threads. UI: the browser page; HTTP request handlers never do long
  work, they submit an engine job and return its id within 50 ms. Engine
  thread: one asyncio event loop that orchestrates every job. Worker threads: one
  ThreadPoolExecutor for parsing, hashing, Excel and ARXML writing.
- asyncio does not make CPU work parallel. Never parse, and never loop over the model,
  on the event loop. Send that work to the pool through `JobContext.run_blocking`.
- lxml releases the GIL while it parses, so parsing many files in the thread pool is
  truly parallel. Use one parser per thread. Pure-Python work on the model is
  GIL-bound: keep it small, and move it to a process pool only if a benchmark proves
  the gain.
- External tools run through `asyncio.create_subprocess_exec` with an argument list,
  never through a shell. DaVinci tools run strictly one at a time.
- Every job is cancellable and reports progress. Throttle progress events to ten per
  second.
- The loaded model is immutable. A reload builds a new model and swaps the reference.
  No locks around model reads.

## Performance budgets (measured on the large synthetic repo)
- Start to usable window: 3 s.
- Cold load of 2,000 ARXML files (500 MB): 30 s. Warm load from cache: 3 s.
- Filter or sort 50,000 rows: 200 ms.
- Excel export of 50,000 rows: 10 s. Import and validation of 500 requests: 5 s.
- ARXML write of 200 operations: 5 s. Peak memory: 2 GB.
- DaVinci check and generation time is set by the Vector tools. Show a live log and
  allow cancel. Never claim to speed it up.
- Measure before you optimize, and report where the time goes.

## Error handling (mandatory)
- Expected problems are data, not exceptions. A rejected request, a missing reference
  or a locked file returns a typed `Finding` with a code, a message for the user and
  the affected object.
- Unexpected problems raise a subclass of `PortToolError` from porttool/core/errors.
- Catch broad exceptions only at four boundaries: CLI entry, engine job, HTTP request
  handler, thread worker. There, log the traceback, show a clear message with the log path, and
  keep the application alive.
- No silent failure: no empty except, no swallowed error.
- Every file write is atomic: write a temporary file in the same folder, flush, then
  `os.replace`. Retry sharing violations three times with backoff.
- Every external call has a timeout. On timeout or cancel, kill the whole process tree.
- Never leave the ECU repo half-written. The pipeline journal makes every apply
  reversible.
- Log to %LOCALAPPDATA%\PortTool\logs with rotation. Logs hold names and paths, never
  file contents.

## Code and test rules
- Type hints everywhere; `mypy --strict` and `ruff check` clean. pydantic at package
  boundaries; plain dataclasses or tuples inside hot loops.
- Tests first. Each package has unit tests under tests/unit/<package> with at least 90
  percent line coverage, plus one named test for every corner case in its prompt.
- Tests run without DaVinci: use synthetic data and the fake DaVinci tools.
- Work on your branch. Touch only your folders. Small commits.
- If anything is ambiguous, or a tool behaves differently from your prompt, stop and
  say so. Do not guess.

## Hand-off
End your work with HANDOFF.md in your package folder: what is done, what is not, the
public API, how to run the tests, measured timings, open questions.

## Decisions taken on 2026-11-11 (the owner has no Claude on the Windows laptop)
- Everything is built and tested in this sandbox, then shipped as a zip the owner runs on
  Windows. Nobody can debug interactively on the target machine, so: prefer boring,
  dependency-light code; make every DaVinci-specific detail configurable in porttool.toml
  rather than hard-coded; fail with a clear message and a log path, never a traceback.
- GUI: no Qt binding can be installed or tested here, so the GUI is a local web interface
  served by the tool on 127.0.0.1 and opened in the default browser. Plain HTML, CSS and
  JavaScript, no CDN, no npm build step, no external requests. It is tested here with
  headless Chrome through Playwright.
- CLI: click (installed here, so the CLI is tested). Not Typer.
- Discovery: there is no Agent 0 run on the real repo. Instead `porttool discover`
  gathers the same facts on the Windows laptop and writes a report the owner sends back.
  Until then every DaVinci-specific behaviour is provisional and marked PENDING-DISCOVERY.
- Dependencies allowed at runtime: lxml, openpyxl, XlsxWriter, pydantic, click, psutil,
  plus the standard library. Nothing else.

## Wiring between packages (fixed names; do not change them)
- `porttool.reader.ArxmlReader()` implements `Reader`.
- `porttool.workbook.XlsxWorkbookIO()` implements `WorkbookIO`.
- `porttool.validator.ChangeValidator()` implements `Validator`.
- `porttool.writer.ArxmlWriter()` implements `Writer` (no required constructor arguments).
- `porttool.davinci.DaVinciToolRunner(config)` implements `DaVinciRunner`;
  `porttool.davinci.davinci_gui_findings(config)` returns Findings when DaVinci Developer or
  Configurator seems to have the workspace or project open.
- `porttool.pipeline.build_service()` returns the `AppService` implementation; it creates
  the Engine and wires all of the above.
- `porttool.gui.serve(service, ...)` starts the local web interface for an `AppService`.
- Package settings live in their own table of porttool.toml and are read with
  `config.extra("<table>")`: `[davinci]`, `[writer]`, `[pipeline]`, `[gui]`.
- The root CLI (`porttool/cli.py`) finds every `porttool/<package>/cli.py` that defines
  `register_cli(group)`.

## Safe defaults for an unseen project
- DaVinci steps are OFF by default: `[pipeline] davinci_steps = []`. Out of the box, apply
  edits the workspace ARXML on a new git branch, verifies by reloading, commits, and stops.
  The owner enables "check", "script", "validate" and "generate" one by one after
  `porttool davinci doctor` and `porttool discover run` confirmed the command lines.
- Command lines of the Vector tools are templates in `[davinci.commands]`. The built-in
  defaults are guesses and are labelled UNVERIFIED everywhere they are shown.
