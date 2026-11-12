# HANDOFF: porttool/davinci (Agent 6, DaVinci runner)

Wraps the Vector command-line tools DVWspChecker, DVImEx, DVSwcGen (DaVinci Developer
Classic) and DVCfgCmd (DaVinci Configurator 5).

**Read this first.** Nobody on this project has seen the real flags, output or exit codes
of these tools, and nothing here has run on Windows or against a real tool. Therefore
nothing about the real tools is hard-coded: every command line is a template in
porttool.toml, every output pattern is a setting, and every built-in default is a guess
that is labelled UNVERIFIED where it is shown. All tests use the fake tools of
`tests/fakes`. The Groovy script was never executed at all.

## What is done

- `DaVinciToolRunner(config)` implements `porttool.core.interfaces.DaVinciRunner`
  (checked by mypy in `test_runner_implements_the_contract`).
- Settings: table `[davinci]`, read with `config.extra("davinci")` into a typed object.
  A bad value is a Finding (`DVR-CFG-*`, `DVR-TPL-*`) and never an exception.
- One function starts every tool (`procrun.run_process`): argument list, no shell, stdin
  closed, explicit working folder (repo root) and environment, `CREATE_NO_WINDOW` through
  `getattr(subprocess, "CREATE_NO_WINDOW", 0)` with no platform branch, both streams read
  concurrently, complete output in a log file, throttled `ctx.progress`, per-action
  timeout, tree kill with psutil on timeout, cancel and any exception, kill confirmed,
  one process-wide gate so that tools never overlap.
- Adapters `check_workspace`, `import_arxml`, `generate_contract_headers`,
  `run_script_task`, `validate_project`, `generate`, each returning `ToolResult`.
- `doctor(ctx)`: starts no tool; every finding that asks for a change names the exact
  porttool.toml line.
- `davinci_gui_findings(config)`: GUI processes and lock files.
- Script task: `write_script_request`, `read_script_request`, `read_script_result`,
  `manual_mapping_instructions`, plus `groovy/PortToolTasks.dvgroovy` and its README.
- CLI: `porttool davinci doctor | check | validate | generate [--modules Rte,Os] |
  show-commands`.
- A named test for every corner case of the task (table below).

## What is not done

- Nothing was run on Windows or with a real Vector tool. See "Unverified on Windows".
- The Groovy script is unexecuted and untested. Only its text is checked (marking, tag
  list, format words shared with Python, balanced braces, info lines free of the words
  PortTool counts as failures). It must be treated as a draft.
- Line coverage was not measured (pytest-cov is not installed, and `python -m trace` on
  tests that pipe megabytes was not worth its run time). No percentage is claimed.
- No Windows Job Object. It would make the tree kill airtight; it needs Win32 calls that
  cannot be tested here. See "Unverified on Windows", item 3.
- UTF-16 output is supported only when named (`encoding = "utf-16-le"`); `"auto"` does
  not detect it.
- Paths longer than 260 characters get no `\\?\` prefix when passed to a tool.

## Public API (`from porttool.davinci import ...`)

```python
runner = DaVinciToolRunner(config, log_dir=None)     # never raises for bad settings
await runner.doctor(ctx)                              -> tuple[Finding, ...]
await runner.check_workspace(ctx)                     -> ToolResult
await runner.import_arxml(arxml, ctx)                 -> ToolResult
await runner.generate_contract_headers(swc_names, ctx) -> ToolResult
await runner.run_script_task(request_json, result_json, ctx) -> ToolResult
await runner.validate_project(ctx)                    -> ToolResult
await runner.generate(modules, ctx)                   -> ToolResult   # () = config.generate_modules

davinci_gui_findings(config, *, processes=None)       -> tuple[Finding, ...]

ScriptEvent(swc, runnable, event_path, task, position=None, position_after_event=None)
write_script_request(events, path, *, apply_solving_actions=True, save_project=True)
read_script_request(path)                 -> (tuple[ScriptEvent, ...], tuple[Finding, ...])
read_script_result(path, expected=())     -> ScriptResult(ok, complete, saved, events,
                                                          findings, unconfirmed)
manual_mapping_instructions(events)       -> list[str]

load_settings(config)                     -> (DaVinciSettings, tuple[Finding, ...])
porttool.davinci.cli.register_cli(group)
```

More on the runner (not part of the contract, useful to the pipeline and the GUI):
`runner.settings`, `runner.settings_findings`, `runner.log_dir()`,
`runner.last_outcome(action)` (kept output lines `tail`, matches, exit code),
`runner.rendered_commands()` (what `show-commands` prints), `runner.lookup(tool)`.

### What a caller must know

- **Raised, not returned:** `ToolTimeoutError` (timeout), `LicenseError` (a license line in
  the output or in a report file), `JobCancelled` or asyncio's `CancelledError` (cancel;
  the engine turns the second into the first), `ToolRunError` (the log file cannot be
  written, or something unexpected while the tool ran). In every case the process tree
  was killed first and the gate is free again. `detail` holds the log path.
- **Returned as `ToolResult(ok=False, exit_code=None, log_file=None)`:** everything that
  stops a tool from being started (settings error, executable missing, a file that does
  not exist, unresolved placeholder, empty module or SWC list, bad script request).
- **`ok`** = exit code is a success code of the action AND no error line AND no error line
  in a scanned report file AND no process survived a kill; for `run_script_task` also
  AND the result file confirms every event of the request.
- Findings of a run: one per matching line, at most 20 per kind, each with its line
  number in the log and `file` = log path; a 21st finding counts the rest.
- `run_script_task` deletes an old result file before the run, reads the request to
  learn the expected events, and appends the findings of `read_script_result` to the
  `ToolResult`. For the manual steps call
  `manual_mapping_instructions(read_script_result(result_json, events).unconfirmed)`, or
  `manual_mapping_instructions(events)` when the step is disabled or raised.
- `davinci_gui_findings` returns only warnings. They are hints (a GUI may have another
  project open; a lock file may be left from a crash). Suggested policy for the
  pipeline: any finding means ask the user before files are changed.
- Keep request and result files outside the repo (cache folder), or the working tree
  becomes dirty.
- Two runs requested at the same time never overlap. Which one goes first is decided by
  who reaches the gate first, not by the order of the calls.
- The output of a tool is processed on the engine loop (decode, match, write the log):
  about 4 MB/s here. A tool that prints a lot keeps the loop busy in slices of one
  64 KiB chunk (about 15 ms each).

## Settings: every key of `[davinci]` with its default

Relative paths are taken from the repo root. An unknown key is a warning
(`DVR-CFG-UNKNOWN`), a wrong type is an error (`DVR-CFG-TYPE`) and the default is used.

| Key | Default | Meaning |
| --- | --- | --- |
| `[davinci.executables] dvwspchecker`, `dvimex`, `dvswcgen`, `dvcfgcmd` | `""` | Path of the executable, or of its folder. Empty: search `<Tool>.exe` in `developer_bin`, then `sip_root` (table `[paths]`), each in `DaVinciConfigurator/Core`, `Core` and the folder itself; the name is compared without case. |
| `[davinci.commands] check`, `import`, `contract_headers`, `script_task`, `validate`, `generate` | built-in guesses below, UNVERIFIED | Argument-list template. A template written here counts as verified. |
| `[davinci.results.success_exit_codes] <action>` | `[0]` | Exit codes that mean success, per action. |
| `[davinci.results] error_patterns` | `["(?i)error"]` | A line matching one is an error line. |
| `[davinci.results] warning_patterns` | `["(?i)warning"]` | A line matching one is a warning line. |
| `[davinci.results] license_patterns` | one pattern: `licen[sc]e` within 80 characters of `not found`, `missing`, `expired`, `unavailable`, `could not`, `cannot`, `unreachable`, `not available`, `no valid`, `invalid`, `denied`, `failed` (either order, no case) | A matching line raises `LicenseError`. Tried before the other patterns. |
| `[davinci.results] ignore_patterns` | `(?i)\b(?:error\|warning)s?(?:\(s\))?\s*[:=]\s*0\b` and `(?i)\b(?:0\|no)\s+(?:error\|warning)s?\b` | Text cut out of a line before the error and warning patterns are tried, so "0 errors, 2 warnings" is a warning line and "Errors: 0" is nothing. Set `[]` for the plain behaviour. |
| `[davinci.results] report_globs` | `[]` | Files the tool writes (logs, reports) to scan with the same patterns. Relative to the repo root or absolute; `{log_dir}` is replaced. Only files changed since the run started are read, at most 20. |
| `encoding` | `"auto"` | `"auto"`: each line as UTF-8, else the ANSI code page (`locale.getencoding()`) with replacement characters. Or a codec name (`"cp1252"`, `"cp850"`, `"utf-16-le"`). |
| `max_kept_output_lines` | `2000` | Lines kept in memory per run. The log file always holds everything. |
| `gui_process_names` | `["DaVinciDEV.exe", "DaVinciCFG.exe", "DVCfg.exe"]` | PENDING-DISCOVERY. Compared without case, with or without `.exe`. |
| `lock_file_globs` | `["*.lock", "*.lck", "~*"]` | PENDING-DISCOVERY. Searched in the folder of the .dcf and of the .dpa (not below). |
| `script_dir` | the `groovy` folder of this package | Value of `{script_dir}`. |
| `script_task` | `"PortToolMapEvents"` | Value of `{script_task}`. |
| `[davinci.env] NAME = "value"` | none | Extra environment variables for every tool. |
| `log_dir` | `tools` below the folder of the PortTool log (`--log-dir`, else `%LOCALAPPDATA%\PortTool\logs`) | Folder of the tool logs; value of `{log_dir}`. |
| `max_tool_logs` | `200` | Older tool logs are deleted beyond this number. |
| `leftover_children` | `"kill"` | What happens to processes a tool started and left running after it ended: `"kill"` or `"report"`. |

Timeouts come from `[timeouts]` (`config.timeouts`): check `workspace_check_s`, import
`import_s`, script_task `script_task_s`, validate `validate_s`, generate and
contract_headers `generate_s`.

Placeholders: `{exe} {dcf} {dpa} {arxml} {modules} {modules_csv} {swcs} {swcs_csv}
{script_dir} {script_task} {request_json} {result_json} {log_dir}`. An argument with a
list placeholder (`{modules}`, `{swcs}`) is repeated once per item. `{{` and `}}` are
literal braces. `{swcs_csv}` is an addition to the list in the task. Before a start,
`{exe}`, `{dcf}`, `{dpa}`, `{arxml}`, `{request_json}` must be existing files and
`{script_dir}` an existing folder; a first argument that is not `{exe}` must be an
existing file or a program on PATH.

Environment of every tool: the environment of PortTool, then `[davinci.env]`, then
`PORTTOOL_LOG_DIR`; for the script task also `PORTTOOL_REQUEST_JSON` and
`PORTTOOL_RESULT_JSON`.

## Built-in command templates: ALL UNVERIFIED

These are guesses (`settings.BUILTIN_COMMANDS`). No flag below has been checked against
a real tool. `porttool davinci doctor` prints each as a ready-made porttool.toml line.

```toml
[davinci.commands]
check            = ['{exe}', '-w', '{dcf}']                                   # DVWspChecker
import           = ['{exe}', '-w', '{dcf}', '-i', '{arxml}']                  # DVImEx
contract_headers = ['{exe}', '-w', '{dcf}', '-c', '{swcs}']                   # DVSwcGen
script_task      = ['{exe}', '-p', '{dpa}', '-s', '{script_task}', '--scriptLocations', '{script_dir}']
validate         = ['{exe}', '-p', '{dpa}', '-s', 'PortToolValidate', '--scriptLocations', '{script_dir}']
generate         = ['{exe}', '-p', '{dpa}', '-g', '-m', '{modules_csv}']      # DVCfgCmd
```

Notes: no validate-only flag of DVCfgCmd is known to me, so the guess for `validate` is
a second script task in the same Groovy file. The module names for `generate` may have
to be definition paths (`/MICROSAR/Rte`) instead of `Rte`: set `[generate] modules`.

## Finding codes (prefix DVR-)

| Code | Severity | Meaning |
| --- | --- | --- |
| DVR-CFG-TYPE | error | A setting has the wrong type or value; the default is used. |
| DVR-CFG-UNKNOWN | warning | Unknown key in `[davinci]` or one of its tables. |
| DVR-CFG-REGEX | error | A pattern is not a valid regular expression. Blocks every action. |
| DVR-CFG-ENCODING | error | Unknown encoding name. Blocks every action. |
| DVR-TPL-NOT-LIST | error | A template is not a list of texts. Blocks that action. |
| DVR-TPL-EMPTY | error | A template is empty or renders to no program. |
| DVR-TPL-UNKNOWN | error | A template uses an unknown placeholder. |
| DVR-TPL-LIST-MIX | error | Two list placeholders in one argument. |
| DVR-TPL-INVALID | error | An unusable template was asked to render. |
| DVR-TPL-UNRESOLVED | error | A placeholder has no value for this action (`dcf_file` not set, `{arxml}` in `check`, ...). Tool not started. |
| DVR-TPL-UNVERIFIED | warning | The command line is a built-in guess. From doctor (with the line to add) and in every result of such a run. |
| DVR-EXE-MISSING | error; in doctor warning unless the step is enabled | Executable not found; names the line for `[davinci.executables]`. |
| DVR-PATH-MISSING | error; in doctor as above | A file or folder a command needs does not exist. |
| DVR-PATH-NOT-SET | doctor: warning or error | `dcf_file` or `dpa_file` is not set; names the line for `[paths]`. |
| DVR-ARG-EMPTY | error | `generate` without any module, or contract headers without any SWC. |
| DVR-LOG-DIR | error | The log folder cannot be used; names the `log_dir` line. |
| DVR-RUN-SPAWN | error | The tool could not be started (operating system error). |
| DVR-RUN-EXIT | error | Exit code is not a success code; names the `success_exit_codes` line. |
| DVR-RUN-ERROR-LINE | error | One error line (log or report file), with line number. |
| DVR-RUN-WARNING-LINE | warning | One warning line, with line number. |
| DVR-RUN-ORPHAN | warning | A process the tool started was still running after it ended (stopped or left, by setting). |
| DVR-RUN-KILL-FAILED | error | A process could not be stopped. |
| DVR-GUI-RUNNING | warning | A DaVinci GUI process is running (name, pid). |
| DVR-GUI-LOCKFILE | warning | A lock file lies next to the .dcf or .dpa. |
| DVR-GUI-SCAN-FAILED | warning | The process list could not be read. |
| DVR-SCRIPT-REQUEST-MISSING / -MALFORMED | error | The request file cannot be used. Tool not started. |
| DVR-SCRIPT-RESULT-STALE | error | An old result file cannot be removed. Tool not started. |
| DVR-SCRIPT-RESULT-MISSING / -MALFORMED / -PARTIAL | error | No result, unreadable result, or `complete` not true. |
| DVR-SCRIPT-STEP-FAILED | error | A step of the script recorded a failure. |
| DVR-SCRIPT-EVENT-MISSING | error | An event of the request is not in the result. |
| DVR-SCRIPT-EVENT-FAILED | error | The script could not map an event. |
| DVR-SCRIPT-TASK-MISMATCH | error | The event is mapped to another task than asked. |
| DVR-SCRIPT-NOT-SAVED | error | Events were mapped but the project was not saved. |
| DVR-SCRIPT-VALIDATION | warning | Validation errors remain after the solving actions. |
| DVR-SCRIPT-OK | info | All requested events confirmed. |
| DVR-SCRIPT-UNVERIFIED, DVR-SCRIPT-FILE-MISSING | doctor | The Groovy script is experimental / is not where `script_dir` points. |
| DVR-DOC-EXE, -TPL, -PATH, -LOG, -NO-RUN | info | Doctor: what was found; and that no tool was started. |

## Corner cases and their named tests

All in `tests/unit/davinci/test_corner_cases.py` unless a file is named.

| Corner case | Test |
| --- | --- |
| Executable missing | `test_executable_missing`, `..._when_the_configured_path_is_wrong`, `..._when_the_template_names_the_program_itself` |
| Path with spaces and non-ASCII characters | `test_path_with_spaces_and_non_ascii_characters` |
| License missing / expired / server unreachable | `test_license_missing_expired_or_server_unreachable[...]` |
| Tool hangs with no output: timeout, tree and child killed | `test_tool_hangs_with_no_output_timeout_tree_killed_child_killed_too` |
| Tool prints megabytes: memory capped, log complete | `test_tool_prints_megabytes_memory_capped_log_complete` (tracemalloc peak below half the output size) |
| Non-UTF-8 output | `test_non_utf8_output` |
| Exit code 0 with error lines | `test_exit_code_0_with_error_lines` |
| Non-zero exit, only warnings, code listed as success | `test_non_zero_exit_with_only_warnings_and_that_code_listed_as_success` |
| Crash exit code | `test_crash_exit_code`; hex form: `test_runner.py::test_windows_crash_code_is_shown_in_hex` |
| Child that outlives the parent | `test_child_that_outlives_the_parent`; pipes held, and the "report" policy: `test_procrun.py` |
| Cancel during a run | `test_cancel_during_a_run` (real Engine); `test_procrun.py::test_cancel_during_a_run_kills_the_tree`, `::test_asyncio_cancellation_kills_the_tree` |
| Two runs requested at once | `test_two_runs_requested_at_once_are_serialised_by_the_lock` (real Engine); two event loops: `test_procrun.py::test_two_runs_from_two_event_loops_are_serialised_too` |
| Unresolved placeholder | `test_unresolved_placeholder_starts_nothing`, `test_unknown_placeholder_starts_nothing` |
| Template that is not a list | `test_template_that_is_not_a_list_starts_nothing` |
| dpa or dcf missing | `test_dcf_missing`, `test_dpa_missing` (each: not set, and set but gone) |
| Script result missing / malformed / partial | `test_script_result_missing_malformed_partial`; in detail in `test_script.py` |
| A trigger whose task is not in the result | `test_trigger_whose_task_is_not_in_the_result` |
| GUI process running (injected process list) | `test_gui_process_running_with_an_injected_process_list` |
| Lock file present | `test_lock_file_present` |

## Decisions I took where the task left room (please confirm or correct)

1. **Gate instead of a literal `asyncio.Lock`.** The task says "one global asyncio lock".
   An `asyncio.Lock` belongs to one event loop, and `porttool.engine.run_sync` creates a
   new engine and loop per CLI command, so a module-level lock would break on its second
   loop. `procrun.TOOL_GATE` is a process-wide first-come-first-served gate that works
   across loops, polls every 50 ms and can be cancelled while waiting. Same guarantee
   (tools never overlap), tested within one loop, across two loops and through the Engine.
2. **`ignore_patterns` with a non-empty default.** With the literal default
   `(?i)error`, a summary line such as "0 errors" would make every successful run fail.
   The default cuts zero counts out of a line before matching. This is an addition to
   the task; set `ignore_patterns = []` to switch it off.
3. **More words in the license default** than the task lists (`cannot`, `unreachable`,
   `not available`, `no valid`, `invalid`, `denied`, `failed`), so that "license server
   unreachable" is recognised.
4. **License line plus hang** raises `LicenseError`, not `ToolTimeoutError`: the message
   says both.
5. **Script paths go through environment variables**, because how DVCfgCmd passes
   arguments to a script task is unknown.
6. **Processes left running after the tool ended** are given 2 s, then stopped
   (`leftover_children = "kill"`) and reported as a warning. `"report"` leaves them.
7. **The kill is blocking and has no `await`**, so a second cancellation cannot interrupt
   it. It returns when the processes are gone (milliseconds), at most after 5 s.
8. **Doctor severity:** a missing executable or file is an error only when a step that
   needs it is listed in `[pipeline] davinci_steps` (names taken from SHARED_CONTEXT:
   check, script, validate, generate); otherwise a warning. Out of the box doctor
   therefore exits with 0 and lists what to add.
9. **`run_script_task` judges the result file itself**, so the pipeline gets one verdict.
10. **Pipe handles of an ended tool** that a surviving child still holds are closed
    through `process._transport` (a private asyncio attribute, read with `getattr`).
    There is no public call for it.
11. **Tool logs** are pruned to the newest 200.
12. **CLI exit code 1** also when a tool ran and failed without an error finding.
13. The fake tools are started in tests either through templates with `sys.executable`,
    or as a copy of the fake script named `<Tool>.exe` (it has a shebang line), which
    exercises `{exe}`, the search and the built-in templates.

## How to run the tests

```
python3 -m pytest tests/unit/davinci -q
mypy --strict porttool/davinci tests/unit/davinci
ruff check porttool/davinci tests/unit/davinci
```

Tails (this sandbox, Python 3.13; the first with `-o addopts=""` to get the summary line):

```
======================= 160 passed, 2 skipped in 23.49s ========================
Success: no issues found in 19 source files
All checks passed!
```

The two skipped tests are `windows_only`. The suite was run six times in a row with
`-W error` without a failure. One test of mine was flaky before that (it assumed which of
two concurrent runs starts first) and was corrected.

## Measured (2 shared cores, noisy, pessimistic)

- Whole davinci suite: 23 to 30 s over eight runs (most of it waiting for fake tools, timeouts, kills).
- One run of a fake tool that prints one line, from adapter call to `ToolResult`: 0.05 s.
- 20 MB of output (205,605 lines): 4.6 s, about 4.4 MB/s, 2000 lines kept in memory.
  The time goes into decoding, pattern matching and writing the log, on the engine loop.
- 4 MB of output with 50 kept lines: tracemalloc peak below 2 MB (asserted in a test).
- Timeout of 1.0 s on a hanging tool with a child: the error is raised after 1.04 s.
- Doctor on a configured project: 4 ms, no process started.

## Unverified on Windows

Nothing in this package has run on Windows. In particular:

1. **Hidden window.** `creationflags=CREATE_NO_WINDOW` is passed (asserted by a spy test);
   that no console window flashes was not seen.
2. **asyncio subprocess on the Proactor loop**: pipe reading with `read()`, `wait()`,
   and closing the transport of an ended process whose pipes a child still holds.
3. **Tree kill.** psutil enumerates children through the parent id. A child whose parent
   ended before PortTool looked is found by `orphans_of` (Windows keeps the parent id;
   a creation-time window guards against a reused process id). That function is tested
   with fake process objects only. Children are looked for at 0.05 s, 0.1 s, 0.2 s ...
   up to every 2 s while the tool runs. A Job Object would close the remaining gap.
   Tests waiting for Windows: `test_windows_process_tree_cmd_then_python_is_killed_on_timeout`,
   `test_windows_child_started_just_before_the_tool_ends_is_still_found`.
4. **Exit codes of crashes** (0xC0000005 arrives as 3221225477): only the formatting is
   tested.
5. **Output encoding.** `"auto"` falls back to the ANSI code page. Console programs often
   print in the OEM code page (cp850, cp437): if umlauts look wrong, set
   `encoding = "cp850"`. Not tested with a real tool.
6. **.cmd or .bat as executable**, UNC paths, paths over 260 characters.
7. **Ctrl+C in the CLI during a tool run**: the engine cancels the job and the tree is
   killed; how Windows delivers Ctrl+C to a child without a console was not seen.
8. **GUI detection**: real process names, access rights of `psutil.process_iter`, real
   lock file names.
9. `%LOCALAPPDATA%` default of the log folder (tested by setting the variable).

## PENDING-DISCOVERY assumptions

- All six command templates, and whether each tool has the action at all.
- Success exit codes (assumed `[0]`), the error, warning and license texts, whether the
  tools write their messages to stdout or only to a log file (then set `report_globs`).
- Output encoding.
- Executable names `<Tool>.exe` and the three search folders.
- GUI process names `DaVinciDEV.exe`, `DaVinciCFG.exe`, `DVCfg.exe`; lock file names.
- Module names for generation (`Rte`, `Os` or definition paths).
- Whether DVCfgCmd picks up a changed Developer workspace by itself before the script
  task runs.
- The whole Groovy script: tags `[A1]` to `[A14]` at its top (script task syntax, logger,
  transaction, model access below `/ActiveEcuC`, container and value calls, the names
  `RteSwComponentInstance`, `RteEventToTaskMapping`, `RteEventRef`, `RteMappedToTaskRef`,
  `RtePositionInTask`, `OsTask`, validation results and solving actions, `saveProject()`,
  JSON classes, environment variables).
- The editor and menu names in `manual_mapping_instructions` were written from memory;
  the text says so.

## Contract change requests

None is blocking.

1. `ToolTimeouts` has no entry for DVSwcGen; contract headers use `generate_s`. Consider
   `contract_headers_s`.
2. `ToolResult` carries no output lines. The pipeline can fill `JobView.log_tail` from
   `runner.last_outcome(action).tail`, which is outside the contract. Consider
   `ToolResult.output_tail: tuple[str, ...]`.
3. `DaVinciRunner.run_script_task` returns only `ToolResult`, so the events that still
   need manual mapping are not in the contract. The pipeline has to call
   `read_script_result(result_json, events).unconfirmed` itself (cheap). Consider a
   contract type for it.
4. `AddTrigger.position_after_event` is an event path, `TaskMapping.position` a number.
   `ScriptEvent` carries both; the script resolves the path to a number.
5. SHARED_CONTEXT says "Do not invent CLI flags or automation API calls" and also that
   built-in defaults are guesses labelled UNVERIFIED. I followed the second (and the
   task). The two sentences should be reconciled in the shared context.
6. `[pipeline] davinci_steps` is read here (for doctor's severity only) with the step
   names check, script, validate, generate. If the pipeline uses other names, tell me or
   change `settings.STEP_OF_ACTION`.
7. `davinci_gui_findings` has an extra keyword `processes` (for tests); the fixed
   signature `davinci_gui_findings(config)` works unchanged.

## Open questions

- Should a GUI finding block an apply, or only ask the user? (This package only reports.)
- Should `leftover_children` default to `"report"`? A license helper that is meant to
  stay alive would be stopped by `"kill"`.
- Should doctor also call `porttool discover` output (the saved help texts) and compare
  flags? Not done: the report format has no contract type.
